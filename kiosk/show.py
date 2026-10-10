"""The show controller: runs each visitor's turn, from idle back to idle.

One async method per state, each returning the next state's name. Events that a
state does not expect are dropped. Every way out of a turn clears the event bus
and resets the studio, so the next visitor always starts clean and the organiser
never needs to touch the laptop. Pause and offline only take effect between
visitors.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import random
from typing import Callable

from kiosk.agent import AgentGaveUp, AgentResult, Job, ToolContext
from kiosk.events import Event
from kiosk.images import ImageBlocked, ImageFailed
from kiosk.llm import LlmUnavailable
from kiosk.store import ImageRecord

log = logging.getLogger(__name__)

POLL_S = 0.5  # how often idle/paused/offline re-check the pause and online flags
MAX_TWEAK_TAPS = 4

MSG_NOT_CAUGHT = "I didn't catch that."
MSG_ASK_ORGANISER = "Ask the organiser to type it"
MSG_TRY_ELSE = "Let's try something else!"
MSG_TIRED = "The image machine is tired! Try again"


def _is(*kinds: str) -> Callable[[Event], bool]:
    return lambda e: e.kind in kinds


class Show:
    def __init__(self, cfg, bus, hub, store, safety, llm_agent, scripted_agent, studio,
                 images, uploader, voice, takehome: Callable[[ImageRecord], dict],
                 poll_s: float = POLL_S):
        self.cfg, self.bus, self.hub, self.store, self.safety = cfg, bus, hub, store, safety
        self.llm_agent, self.scripted_agent = llm_agent, scripted_agent
        self.studio, self.images, self.uploader, self.voice = studio, images, uploader, voice
        self.takehome = takehome
        self.poll_s = poll_s
        self.online = True  # set by the health checker (Task 19)
        self.paused = False
        self._states = {
            "idle": self._idle, "choose_mode": self._choose_mode, "listen": self._listen,
            "confirm": self._confirm, "filter": self._filter, "agent": self._agent,
            "takehome": self._takehome,
        }
        self._new_visitor()

    def _new_visitor(self) -> None:
        self.state = "idle"
        self.mode = ""
        self.request = ""
        self.image_prompt = ""
        self.blocked = 0
        self.stt_failures = 0
        self.using_preset = False
        self.scripted_only = False
        self.record_now = False  # confirm's hold_start: listen starts recording at once
        self.recording = False

    # ----- public -----

    async def run_forever(self) -> None:
        while True:
            try:
                if self.paused or not self.online:
                    await self._hold()
                await self.run_visitor()
            except Exception as exc:  # noqa: BLE001 - last line of defence; keep going
                log.error("show loop error: %s", type(exc).__name__)
                await asyncio.sleep(self.poll_s)

    async def set_paused(self, p: bool) -> None:
        self.paused = p

    async def run_visitor(self) -> None:
        """Run one turn from idle back to idle. Never raises (except cancellation)."""
        self._new_visitor()
        started = False
        try:
            nxt = await self._enter("idle")
            while nxt not in (None, "idle"):
                started = True
                nxt = await self._enter(nxt)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the show must never get stuck
            started = True
            # Type only: messages from network clients could carry URLs or details.
            text = f"show error in {self.state}: {type(exc).__name__}"
            log.error(text)
            with contextlib.suppress(Exception):
                await self.hub.send({"type": "log", "text": text})
        finally:
            if started:
                await self._cleanup()

    # ----- helpers -----

    async def _enter(self, state: str) -> str | None:
        self.state = state
        await self._send_state(state)
        return await self._states[state]()

    async def _send_state(self, state: str) -> None:
        await self.hub.send({"type": "state", "state": state, "mode": self.mode})

    async def _message(self, text: str) -> None:
        await self.hub.send({"type": "message", "text": text})

    async def _hold(self) -> None:
        """Stay paused/offline (between visitors) until both clear."""
        self.mode = ""
        shown = None
        while self.paused or not self.online:
            held = "paused" if self.paused else "offline"
            if held != shown:
                await self._send_state(held)
                shown = held
            await asyncio.sleep(self.poll_s)
        self.bus.clear()  # presses made while held are not a new visitor

    async def _cleanup(self) -> None:
        if self.recording:
            self.recording = False
            try:
                await self.voice.stop()
            except Exception as exc:  # noqa: BLE001
                log.warning("voice stop failed: %s", type(exc).__name__)
        self.bus.clear()
        await self._reset_studio()

    async def _reset_studio(self) -> None:
        try:
            await self.studio.reset()
        except Exception as exc:  # noqa: BLE001
            log.warning("studio reset failed: %s", type(exc).__name__)

    async def _next_event(self, timeout: float, accept: Callable[[Event], bool],
                          captions: bool = False) -> Event | None:
        """The first accepted event within timeout, or None. Other events are dropped;
        with captions=True, partial captions are forwarded to the TV."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while (left := deadline - loop.time()) > 0:
            ev = await self.bus.next(left)
            if ev is None:
                return None
            if captions and ev.kind == "caption":
                await self.hub.send({"type": "caption", "text": ev.text or "", "final": False})
            elif accept(ev):
                return ev
        return None

    async def _set_request(self, text: str) -> None:
        self.request = text.strip()[: self.cfg.limits.max_request_chars]
        await self.hub.send({"type": "caption", "text": self.request, "final": True})

    async def _use_preset(self) -> str:
        self.using_preset = True
        await self._set_request(random.choice(self.cfg.preset_ideas))
        self.image_prompt = self.request
        return "agent"

    async def _blocked(self) -> str:
        self.blocked += 1
        await self._message(MSG_TRY_ELSE)
        if self.blocked >= self.cfg.limits.max_blocked_tries:
            return await self._use_preset()
        return "listen"

    # ----- states -----

    async def _idle(self) -> str | None:
        while True:
            ev = await self.bus.next(self.poll_s)
            if ev is None:
                if self.paused or not self.online:
                    return None
            elif ev.kind in ("taps", "hold_start"):
                return "choose_mode"
            elif ev.kind == "typed_request" and ev.text:
                self.mode = "kid"
                await self._set_request(ev.text)
                return "confirm"

    async def _choose_mode(self) -> str:
        t = self.cfg.timeouts
        ev = await self._next_event(
            t.choose_mode_s, lambda e: e.kind == "hold_end" or (e.kind == "taps" and (e.n or 0) > 1)
        )
        self.mode = "nerd" if ev is not None and ev.kind == "taps" else "kid"
        return "listen"

    async def _listen(self) -> str:
        t = self.cfg.timeouts
        if not self.record_now:
            ev = await self._next_event(
                t.listen_wait_s, _is("hold_start", "typed_request"), captions=True
            )
            if ev is None:
                return "idle"
            if ev.kind == "typed_request":
                await self._set_request(ev.text or "")
                return "confirm"
        self.record_now = False

        self.recording = True
        await self.voice.start()
        # No hold_end within listen_hold_max_s: stop anyway (long-hold auto-stop).
        ev = await self._next_event(
            t.listen_hold_max_s, _is("hold_end", "stt_error", "typed_request"), captions=True
        )
        self.recording = False
        text = (await self.voice.stop()).strip()
        if ev is not None and ev.kind == "typed_request":
            await self._set_request(ev.text or "")
            return "confirm"
        if text and (ev is None or ev.kind != "stt_error"):
            await self._set_request(text)
            return "confirm"

        self.stt_failures += 1
        if self.stt_failures <= self.cfg.limits.max_stt_retries:
            await self._message(MSG_NOT_CAUGHT)
            return "listen"
        await self._message(MSG_ASK_ORGANISER)
        ev = await self._next_event(t.listen_wait_s, _is("typed_request"))
        if ev is None:
            return "idle"
        await self._set_request(ev.text or "")
        return "confirm"

    async def _confirm(self) -> str:
        ev = await self._next_event(self.cfg.timeouts.confirm_s, _is("taps", "hold_start"))
        if ev is not None and ev.kind == "hold_start":
            self.record_now = True
            return "listen"
        return "filter"

    async def _filter(self) -> str:
        try:
            result = await self.safety.check_request(self.request, self.mode)
        except LlmUnavailable:
            log.warning("safety LLM unavailable: using a preset idea and the scripted agent")
            self.scripted_only = True
            return await self._use_preset()
        if not result.allowed:
            return await self._blocked()
        self.image_prompt = result.image_prompt
        return "agent"

    async def _agent(self) -> str:
        job = Job(request=self.request, image_prompt=self.image_prompt, mode=self.mode)
        ctx = ToolContext(
            studio=self.studio, images=self.images, safety=self.safety,
            narrate=self._narrate, on_tool=self._on_tool, choose_tweak=self._choose_tweak,
        )
        if self.mode == "nerd":
            await self.hub.send({"type": "prompt", "text": job.image_prompt,
                                 "model": self.images.model, "llm": self._llm_name()})
        try:
            result = await self._run_agents(job, ctx)
        except ImageBlocked:
            log.info("image model blocked the prompt")
            await self._reset_studio()  # the next attempt types into a clean studio
            if self.using_preset:  # even a preset idea was blocked: give up this turn
                await self._message(MSG_TIRED)
                return "idle"
            return await self._blocked()
        except ImageFailed:
            await self._message(MSG_TIRED)
            return "idle"
        await self._deliver(result)
        return "takehome"

    def _llm_name(self) -> str:
        """The LLM that will run this turn's agent, for the nerd panel."""
        if self.scripted_only:
            return "scripted"
        return getattr(getattr(self.llm_agent, "llm", None), "active", None) or "scripted"

    async def _run_agents(self, job: Job, ctx: ToolContext) -> AgentResult:
        cap = self.cfg.timeouts.agent_cap_s
        if not self.scripted_only:
            try:
                return await asyncio.wait_for(self.llm_agent.run(job, ctx), cap)
            except (AgentGaveUp, TimeoutError) as exc:
                log.info("LLM agent stopped (%s); scripted agent resumes", type(exc).__name__)
        try:
            return await asyncio.wait_for(self.scripted_agent.run(job, ctx), 2 * cap)
        except TimeoutError as exc:
            raise ImageFailed("scripted agent timed out") from exc

    async def _deliver(self, result: AgentResult) -> None:
        """Save and queue the picture, then fill the take-home card before it shows."""
        prompt = result.prompt
        if result.tweak is not None:
            prompt = f"{prompt}. Tweak: {result.tweak}"
        rec = self.store.new_record(prompt, self.mode)
        self.store.save_image(rec.id, result.jpeg)
        self.uploader.enqueue(rec)
        await self.hub.send({"type": "image", "url": f"/local/{rec.id}.jpg"})
        await self.hub.send({**self.takehome(rec), "type": "takehome"})

    async def _takehome(self) -> str:
        await asyncio.sleep(self.cfg.timeouts.takehome_s)
        return "idle"

    # ----- agent callbacks -----

    async def _narrate(self, text: str) -> None:
        await self.hub.send({"type": "narration", "text": text})

    async def _on_tool(self, name: str, args: dict) -> None:
        if self.mode == "nerd":
            await self.hub.send({"type": "tool", "name": name, "args": args})

    async def _choose_tweak(self, options: list[str]) -> str | None:
        """Show the options and wait tweak_s for a choice. None means keep."""
        self.bus.clear()  # presses made while the agent worked are not a choice
        self.state = "tweak"
        await self._send_state("tweak")
        await self.hub.send({"type": "tweaks", "options": list(options)})
        try:
            ev = await self._next_event(self.cfg.timeouts.tweak_s, _is("taps", "tweak_choice"))
        finally:
            self.state = "agent"
            await self._send_state("agent")
        if ev is None:
            return None
        n = min(ev.n or 0, MAX_TWEAK_TAPS) if ev.kind == "taps" else (ev.n or 0)
        return options[n - 1] if 1 <= n <= len(options) else None
