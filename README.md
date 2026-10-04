# AceGPT

AceGPT is a Python Discord bot that chats when mentioned, analyzes image attachments, keeps separate short-term conversation history for each Discord channel, and understands replied-to messages as context. Its initial provider configuration uses the Perplexity Agent API.

## Features

- Responds when a member mentions the bot.
- Analyzes PNG, JPEG, WEBP, and GIF image attachments. Images are encoded locally and sent to the configured AI provider; each image is limited by `MAX_IMAGE_MB` (8 MB by default).
- Keeps recent turns in a separate in-memory history for each channel.
- Uses the message being replied to as context when Discord makes it available.
- Continues a conversation when someone replies directly to an AceGPT answer; otherwise, mention AceGPT to start a new turn.
- Provides `/reset` to clear the current channel's conversation history.
- Joins a voice channel with `/gptsummon`, listens for a spoken “Ace” or “AceGPT” wake phrase, and speaks answers in that voice channel; `/gptdismiss` stops listening. If local TTS fails, it falls back to posting the answer in the text channel.
- Allows the personality, API endpoint, model, history size, image limit, output length, and logging level to be configured through environment variables.
- Runs as a normal long-lived Discord Gateway process and can be packaged as a Docker worker.

## Requirements

- Python 3.11 or newer, or Docker.
- A Discord application with a bot user.
- An API key for Perplexity or another compatible provider.
- For always-on hosting, a service that supports persistent background workers or containers. Vercel Functions are request-based and time-limited, so they are not appropriate for maintaining this bot's long-lived Discord Gateway connection.

## Discord setup

1. Open the [Discord Developer Portal](https://discord.com/developers/applications) and create an application named **AceGPT**.
2. Open **Bot**, add a bot user if needed, and copy its token. Treat this token like a password. Never put it in a public repository or send it in chat.
3. In **Bot → Privileged Gateway Intents**, enable **Message Content Intent**. The code enables the corresponding intent; Discord must also allow it for the application.
4. Open **OAuth2 → URL Generator** and select the `bot` and `applications.commands` scopes.
5. Grant **View Channels**, **Send Messages**, **Read Message History**, and **Use Application Commands**. For voice answers, also grant **Connect** and **Speak**. Copy the generated install URL, open it, choose your server, review the permissions, and authorize the bot.
6. Make sure the bot can see the channel where you want to use it. Mention it in a message to start a conversation.
7. For voice mode, grant the bot role **Connect** and **Speak** access in the voice channel, plus **Send Messages** access in the text channel for command replies and TTS error fallback.

## Voice mode

1. In a text channel, use `/gptsummon` while you are connected to the voice channel where you want AceGPT to listen. AceGPT speaks answers in that same voice channel.
2. Ask a question aloud starting with “AceGPT” or “Ace” (for example, “AceGPT, what is the capital of France?”). Use `/gptdismiss` when finished.
3. Speech recognition runs locally with faster-whisper. The first `/gptsummon` downloads the configured Whisper model (`base.en` by default); allow a little extra time on its first use. Audio is held in memory only and is not saved. Only recognized utterances starting with the wake phrase are sent as text to the configured AI provider. AceGPT speaks the answer using the host computer's local speech engine; it does not need a TTS API key or send the answer to an external TTS service.

Voice receiving uses `discord-ext-voice-recv`, an experimental community extension for discord.py. Keep it updated if Discord voice behavior changes.

## Perplexity setup

1. Create or open an account in the [Perplexity API platform](https://www.perplexity.ai/account/api) and create an API key.
2. The default API endpoint is `https://api.perplexity.ai/v1`, using the Responses API compatibility endpoint and the `fast` preset. Perplexity's Sonar Chat Completions API has migrated to its Agent API; see the [migration guide](https://docs.perplexity.ai/docs/agent-api/migrate-from-sonar/overview) and [OpenAI compatibility guide](https://docs.perplexity.ai/docs/agent-api/openai-compatibility).
3. Image analysis requires a selected Agent API model/preset that accepts image input. Image processing consumes API usage according to the provider's current pricing and account limits.
4. Do not use a regular Perplexity website subscription key as an API key; configure an API key created in the API platform.

## APIs and accounts AceGPT uses

- **Discord API:** Create a Discord application and bot user in the Developer Portal. The bot token belongs in `DISCORD_TOKEN`. Enable **Message Content Intent** so mention-based chat can read message text. The bot uses Discord's Gateway connection for messages and voice, and Discord application commands for `/reset`, `/gptsummon`, and `/gptdismiss`.
- **AI API (Perplexity by default):** Create an API key in the Perplexity API Platform and set it as `AI_API_KEY`. AceGPT uses Perplexity's Agent API through the OpenAI-compatible Responses API endpoint. `AI_BASE_URL` defaults to `https://api.perplexity.ai/v1`; `AI_PRESET=fast` is the default. API usage and billing are controlled by the provider. A Perplexity website subscription is not an API key.
- **Alternative AI providers:** Any provider compatible with the OpenAI Python SDK can be configured. Set `AI_BASE_URL`, `AI_API_KEY`, and `AI_API_MODE`. Use `responses` for a Responses-compatible endpoint; use `chat_completions` and set `AI_MODEL` for a Chat Completions-compatible endpoint. Choose a model that supports image input if you want image analysis.
- **Speech recognition:** faster-whisper runs locally. It downloads the configured model on first use; captured voice audio stays in memory and is not saved. No transcription API key is needed.
- **Text to speech:** pyttsx3 uses the computer's installed speech engine to generate audio locally, and FFmpeg packages that audio for Discord. No external TTS API or TTS key is needed. Install the bot role's **Speak** permission in voice channels. On Windows, the system SAPI voices are used by default.

Keep `DISCORD_TOKEN` and `AI_API_KEY` in `.env` locally or in the hosting provider's secret manager. Never put real credentials in `.env.example`, README files, or commits.

## Run locally on Windows

1. Open this project folder, then copy `.env.example` to a new file named `.env`. Do not edit `.env.example` with real credentials.
2. Open `.env` in a text editor and set at least these values:

   ```dotenv
   DISCORD_TOKEN=your_discord_bot_token
   AI_API_KEY=your_perplexity_api_key
   ```

   The rest of the defaults are already configured for Perplexity Agent API. Keep `.env` on your computer; `.gitignore` excludes it from Git.
3. Install Python 3.11+ if needed. In PowerShell, from this project folder, create and activate a virtual environment and install the dependencies:

   ```powershell
   py -3 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install --upgrade pip
   pip install -r requirements.txt
   ```

   If PowerShell blocks activation, run the environment's Python directly instead:

   ```powershell
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```
4. Start AceGPT:

   ```powershell
   python bot.py
   ```

   Or double-click `run.bat`. It creates the private environment and installs dependencies the first time, then starts AceGPT. To run directly without the launcher:

   ```powershell
   .\.venv\Scripts\python.exe bot.py
   ```
5. Leave the terminal open while the bot is in use. Press **Ctrl+C** to stop it.
6. Mention AceGPT in a Discord channel. Use `/reset` to clear that channel's history.

On macOS or Linux, the equivalent virtual-environment commands are:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python bot.py
```

## Configuration

All settings are read from environment variables. For local use, `python-dotenv` loads them from `.env` at startup.

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `DISCORD_TOKEN` | Yes | — | Bot token from the Discord Developer Portal. |
| `AI_API_KEY` | Yes | — | API key for the configured AI provider. For the default setup, use a Perplexity API key. |
| `AI_BASE_URL` | No | `https://api.perplexity.ai/v1` | OpenAI SDK-compatible API base URL. |
| `AI_API_MODE` | No | `responses` | API format: `responses` for Perplexity Agent API, or `chat_completions` for compatible providers using Chat Completions. |
| `AI_MODEL` | No | empty | Optional model supported by the provider. When blank in Responses mode, AceGPT uses `AI_PRESET`. |
| `AI_PRESET` | No | `fast` | Perplexity Agent API preset used when `AI_MODEL` is blank. |
| `BOT_PERSONALITY` | No | Friendly AceGPT prompt | System instructions that define AceGPT's style and behavior. |
| `MAX_HISTORY_MESSAGES` | No | `20` | Maximum retained conversation entries per channel. This counts user and assistant entries. |
| `MAX_IMAGE_MB` | No | `8` | Maximum size of each image attachment. |
| `MAX_OUTPUT_TOKENS` | No | `800` | Maximum response length requested from the provider. |
| `VOICE_WAKE_WORD` | No | `acegpt` | Spoken word that activates a voice question. “Ace” is also accepted with the default. |
| `VOICE_WHISPER_MODEL` | No | `base.en` | Local faster-whisper model used for English transcription. Downloaded on first `/gptsummon`. |
| `VOICE_LANGUAGE` | No | `en` | Language code used for local speech recognition. |
| `VOICE_RMS_THRESHOLD` | No | `350` | Voice activity sensitivity; lower it if quiet speech is not detected. |
| `VOICE_SILENCE_SECONDS` | No | `0.8` | Silence that ends an utterance before transcription. |
| `VOICE_MAX_UTTERANCE_SECONDS` | No | `12` | Maximum length of an utterance before it is transcribed. |
| `VOICE_TTS_ENABLED` | No | `true` | Speak voice answers aloud; set to `false` to post them in text instead. |
| `VOICE_TTS_VOICE` | No | system default | Optional voice ID from the host speech engine. |
| `VOICE_TTS_RATE` | No | `180` | Speech rate in words per minute, clamped between 80 and 300. |
| `VOICE_TTS_VOLUME` | No | `1.0` | Speech volume from 0.0 to 1.0. |
| `LOG_LEVEL` | No | `INFO` | Python logging level, such as `INFO` or `DEBUG`. |

For a different provider, set `AI_BASE_URL`, `AI_API_KEY`, and the matching `AI_API_MODE`. In `chat_completions` mode, provide the provider's `AI_MODEL`; in `responses` mode, use its compatible Responses API format. The selected model must support image inputs if image analysis is required.

## Docker and persistent-worker hosting

The included `Dockerfile` installs the Linux speech engine and runs `python bot.py` as a foreground process, which is suitable for a Docker-based background worker. `render.yaml` describes a Render worker; it intentionally marks credentials as values that must be added in the hosting dashboard.

General deployment steps:

1. Publish the project source to a GitHub repository, leaving `.env` out of the repository.
2. Create a **Background Worker** or equivalent persistent Docker service from that repository.
3. Set `DISCORD_TOKEN` and `AI_API_KEY` in the host's secret/environment-variable settings. Do not include either value in deployment files, build arguments, or source control.
4. Set optional environment values in the host dashboard if you want to change the default personality, provider, or limits.
5. Deploy one running instance and check its logs for the successful Discord connection message.

Do not run multiple copies with the same bot token unless you have intentionally designed for multiple gateway sessions. In-memory conversation history is local to one process and is not shared across replicas.

## Privacy and security

- Never commit `.env`, paste tokens into source code, or share credentials in messages. If a token is exposed, revoke and replace it in the relevant provider portal.
- `.gitignore` excludes `.env`, and `.dockerignore` excludes local credentials and development files from Docker build context.
- `.env.example` contains placeholders only. Use it as a template, not as the file where you store secrets.
- When AceGPT is mentioned, the message text and any attached image are sent to the configured AI provider so it can generate a reply.
- In voice mode, short audio frames are processed in memory by local speech recognition and are not saved. When someone says the wake phrase, the recognized question text is sent to the configured AI provider; the answer is spoken in the voice channel where `/gptsummon` was used. Speech is synthesized locally; the answer text is not sent to a TTS service. If local speech fails, AceGPT posts a text fallback in the selected text channel. Make sure server members know this and only use the bot in channels where that sharing is appropriate.
- Conversation history is held only in process memory. It is not written to a database or disk and disappears when the bot restarts.
- Logs report operational events and errors; avoid adding message contents or credentials to logs.

## Troubleshooting

**Voice mode does not join, hear speech, or speak answers**

- Grant the bot role **Connect** and **Speak** permissions in the target voice channel.
- Use `/gptsummon` from a text channel after joining the desired voice channel. Confirm Discord shows AceGPT connected.
- Start the spoken question with “Ace” or “AceGPT”; the bot ignores other voice conversation.
- If quiet speech is missed, lower `VOICE_RMS_THRESHOLD` in `.env` and restart.

**The bot is online but ignores mentions**

- Confirm **Message Content Intent** is enabled in the Developer Portal.
- Confirm the bot can view the channel and read its message history.
- Make sure the mention is of the bot account itself.

**`DISCORD_TOKEN` or `AI_API_KEY` is missing**

- Check that the file is named exactly `.env` (not `.env.txt`) and is in the same folder as `bot.py`.
- Confirm the two variables have non-empty values and no extra quotes or spaces around the `=`.
- On a hosted worker, add them in the host's secret settings and redeploy/restart the worker.

**The bot starts but AI replies fail**

- Check the worker terminal/logs for the provider error.
- Confirm `AI_API_KEY`, `AI_BASE_URL`, and the selected model or preset belong to the same provider account and API product.
- Check API billing, model access, usage limits, and provider service status.
- If text works but images fail, check that the selected provider/model supports the image input format.

**`/reset` does not appear**

- Confirm the bot was invited with the `applications.commands` scope.
- Wait briefly for Discord to update commands, then restart the bot and check its logs.

## Project files

- `bot.py` — Discord event handling, per-channel history, image encoding, provider calls, reply context, `/reset`, `/gptsummon`, and `/gptdismiss`.
- `voice_integration.py` — voice capture, local transcription, wake phrase detection, and local TTS playback.
- `requirements.txt` — Python dependencies.
- `.env.example` — Safe template containing no real credentials.
- `.gitignore` — Excludes `.env`, virtual environments, and Python cache files from Git.
- `.dockerignore` — Excludes secrets and local development files from Docker build context.
- `Dockerfile` — Container definition for persistent-worker hosting.
- `render.yaml` — Render Background Worker blueprint.




