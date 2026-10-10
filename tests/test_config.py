from pathlib import Path

from kiosk.config import load_config, load_secrets

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_loads_spec_defaults():
    c = load_config(REPO_ROOT / "config.toml")
    assert c.models.image == "gemini-3.1-flash-lite-image"
    assert c.timeouts.agent_cap_s == 45 and c.limits.max_tool_calls == 10
    assert c.voting.close_at.isoformat() == "2026-10-30T18:00:00+13:00"
    assert len(c.preset_tweaks) == 4


def test_secrets_from_file_and_masked(tmp_path, monkeypatch):
    env = tmp_path / ".env"; env.write_text("GOOGLE_API_KEY=abc123\nOPENROUTER_API_KEY=x\nFALLBACK_LLM_URL=u\nFALLBACK_LLM_KEY=k\nGALLERY_URL=g\nGALLERY_TOKEN=t\n")
    for k in ["GOOGLE_API_KEY","OPENROUTER_API_KEY","FALLBACK_LLM_URL","FALLBACK_LLM_KEY","GALLERY_URL","GALLERY_TOKEN"]:
        monkeypatch.delenv(k, raising=False)
    s = load_secrets(env)
    assert s.google_api_key == "abc123" and "abc123" not in repr(s)
