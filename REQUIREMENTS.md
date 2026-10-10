# REQUIREMENTS
- Image generation using Nano Banana 2 Lite (`gemini-3.1-flash-lite-image`) served from google AI studio (fallback: nano banana 2.1, `gemini-nano-banana-2.1`): 
    - API key: stored in ~/.nerdfest/.env (GOOGLE_API_KEY)
- Speech-to-text using Gemini 3.5 Transcribe Live (`gemini-3.5-transcribe-live`) served from google AI studio (same API key as above, paid tier).
- LLM use GPT-6.1 Sol (`openai/gpt-6.1-sol`) served from OpenRouter:
    - API key: stored in ~/.nerdfest/.env (OPENROUTER_API_KEY)
- Fallback LLM: self-hosted Qwen3.8-27B (`Qwen3.8-27B`) over Tailscale:
    - URL and key: stored in ~/.nerdfest/.env (FALLBACK_LLM_URL, FALLBACK_LLM_KEY)
