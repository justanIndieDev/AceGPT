from __future__ import annotations

import asyncio
import base64
import logging
import os
from collections import defaultdict, deque
from io import BytesIO
from typing import Any

import discord
from discord import app_commands
from dotenv import load_dotenv
from openai import AsyncOpenAI
from voice_integration import VoiceAssistant

load_dotenv()
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("acegpt")


def required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


DISCORD_TOKEN = required("DISCORD_TOKEN")
AI_API_KEY = required("AI_API_KEY")
AI_BASE_URL = os.getenv("AI_BASE_URL", "https://api.perplexity.ai/v1").strip()
AI_API_MODE = os.getenv("AI_API_MODE", "responses").strip().lower()
AI_MODEL = os.getenv("AI_MODEL", "").strip()
AI_PRESET = os.getenv("AI_PRESET", "fast").strip()
BOT_PERSONALITY = os.getenv("BOT_PERSONALITY", "You are AceGPT, a friendly, clever, concise Discord assistant. Be helpful and conversational. Follow server rules and do not claim to have abilities you lack.").strip()
MAX_HISTORY_MESSAGES = max(2, int(os.getenv("MAX_HISTORY_MESSAGES", "20")))
MAX_IMAGE_BYTES = max(1, int(os.getenv("MAX_IMAGE_MB", "8"))) * 1024 * 1024
MAX_OUTPUT_TOKENS = max(64, int(os.getenv("MAX_OUTPUT_TOKENS", "800")))

client_ai = AsyncOpenAI(api_key=AI_API_KEY, base_url=AI_BASE_URL)
intents = discord.Intents.default()
intents.message_content = True
intents.messages = True
intents.voice_states = True
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

# Ephemeral per-process memory, deliberately scoped by channel ID.
histories: dict[int, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=MAX_HISTORY_MESSAGES))
channel_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


def is_image(attachment: discord.Attachment) -> bool:
    content_type = (attachment.content_type or "").lower()
    return content_type.startswith("image/") or attachment.filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif"))


async def make_user_content(message: discord.Message, text: str) -> str | list[dict[str, Any]]:
    images: list[dict[str, Any]] = []
    for attachment in message.attachments:
        if not is_image(attachment):
            continue
        if attachment.size > MAX_IMAGE_BYTES:
            raise ValueError(f"Image {attachment.filename} is larger than the {MAX_IMAGE_BYTES // (1024 * 1024)} MB limit.")
        data = await attachment.read(use_cached=True)
        mime = attachment.content_type or "image/jpeg"
        encoded = base64.b64encode(data).decode("ascii")
        image_url = f"data:{mime};base64,{encoded}"
        if AI_API_MODE == "responses":
            images.append({"type": "input_image", "image_url": image_url})
        else:
            images.append({"type": "image_url", "image_url": {"url": image_url}})
    if not images:
        return text
    text_type = "input_text" if AI_API_MODE == "responses" else "text"
    return [{"type": text_type, "text": text or "Please describe this image."}, *images]


def clean_mention(text: str) -> str:
    if bot.user:
        text = text.replace(f"<@{bot.user.id}>", "").replace(f"<@!{bot.user.id}>", "")
    return text.strip()


async def generate_reply(channel_id: int, user_label: str, prompt: str | list[dict[str, Any]], reply_context: str | None) -> str:
    async with channel_locks[channel_id]:
        current_content: str | list[dict[str, Any]] = prompt
        if reply_context:
            if isinstance(prompt, list):
                text_type = "input_text" if AI_API_MODE == "responses" else "text"
                prompt = [{"type": text_type, "text": f"Context from the message being replied to:\n{reply_context}\n\nYour message: "}, *prompt]
            else:
                prompt = f"Context from the message being replied to: {reply_context}\n\nYour message: {prompt}"
            current_content = prompt

        if AI_API_MODE == "responses":
            response_input: list[dict[str, Any]] = list(histories[channel_id])
            if isinstance(current_content, list):
                user_content: str | list[dict[str, Any]] = [{"type": "input_text", "text": f"{user_label}: "}, *current_content]
            else:
                user_content = f"{user_label}: {current_content}"
            response_input.append({"role": "user", "content": user_content})
            request: dict[str, Any] = {
                "input": response_input,
                "instructions": BOT_PERSONALITY,
                "max_output_tokens": MAX_OUTPUT_TOKENS,
            }
            if AI_MODEL:
                request["model"] = AI_MODEL
            elif AI_PRESET:
                request["extra_body"] = {"preset": AI_PRESET}
            response = await client_ai.responses.create(**request)
            answer = (response.output_text or "").strip()
        elif AI_API_MODE == "chat_completions":
            messages: list[dict[str, Any]] = [{"role": "system", "content": BOT_PERSONALITY}]
            messages.extend(list(histories[channel_id]))
            if isinstance(current_content, list):
                user_content = [{"type": "text", "text": f"{user_label}: "}, *current_content]
            else:
                user_content = f"{user_label}: {current_content}"
            messages.append({"role": "user", "content": user_content})
            response = await client_ai.chat.completions.create(model=AI_MODEL, messages=messages, max_tokens=MAX_OUTPUT_TOKENS)
            answer = (response.choices[0].message.content or "").strip()
        else:
            raise RuntimeError("AI_API_MODE must be 'responses' or 'chat_completions'.")

        if not answer:
            answer = "I couldn't come up with a response just now. Try again?"
        history_prompt = prompt if isinstance(prompt, str) else "[image and message]"
        histories[channel_id].append({"role": "user", "content": f"{user_label}: {history_prompt}"})
        histories[channel_id].append({"role": "assistant", "content": answer})
        return answer

async def send_chunks(message: discord.Message, content: str) -> None:
    # Discord messages are limited to 2000 characters.
    for start in range(0, len(content), 1900):
        await message.reply(content[start:start + 1900], mention_author=False)


@bot.event
async def on_ready() -> None:
    try:
        global_synced = await tree.sync()
        log.info("Synced %d global slash command(s)", len(global_synced))
        for guild in bot.guilds:
            tree.copy_global_to(guild=guild)
            guild_synced = await tree.sync(guild=guild)
            log.info("Synced %d server slash command(s) for guild %s", len(guild_synced), guild.id)
    except discord.HTTPException:
        log.exception("Could not sync slash commands")
    log.info("AceGPT connected as %s", bot.user)


@bot.event
async def on_message(message: discord.Message) -> None:
    if message.author.bot or bot.user is None:
        return

    referenced: discord.Message | None = None
    if message.reference and message.reference.message_id:
        try:
            candidate = message.reference.resolved or await message.channel.fetch_message(message.reference.message_id)
            if isinstance(candidate, discord.Message):
                referenced = candidate
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            referenced = None

    mentioned = bot.user in message.mentions
    replying_to_acegpt = referenced is not None and referenced.author.id == bot.user.id
    if not mentioned and not replying_to_acegpt:
        return

    log.info("Received a text request in channel %s (mention=%s, reply_to_bot=%s)", message.channel.id, mentioned, replying_to_acegpt)
    prompt = clean_mention(message.content) if mentioned else message.content.strip()
    if not prompt and not any(is_image(a) for a in message.attachments):
        prompt = "Say hello and ask what I need."

    reply_context = None
    if referenced is not None:
        excerpt = referenced.content[:1200]
        reply_context = f"{referenced.author.display_name} wrote: {excerpt}" if excerpt else f"{referenced.author.display_name} shared an attachment."

    try:
        prompt_content = await make_user_content(message, prompt)
        async with message.channel.typing():
            answer = await generate_reply(message.channel.id, message.author.display_name, prompt_content, reply_context)
        await send_chunks(message, answer)
        log.info("Sent an AceGPT reply in channel %s", message.channel.id)
    except ValueError as exc:
        await message.reply(str(exc), mention_author=False)
    except Exception:
        log.exception("AI response failed in channel %s", message.channel.id)
        await message.reply("I hit a problem while answering. Check the bot logs or try again in a moment.", mention_author=False)

voice_assistant = VoiceAssistant(generate_reply)


@tree.command(name="gptsummon", description="Bring AceGPT into your voice channel")
async def join_voice(interaction: discord.Interaction) -> None:
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("Use this command in a server.", ephemeral=True)
        return
    if interaction.user.voice is None or interaction.user.voice.channel is None:
        await interaction.response.send_message("Join a voice channel first, then use /gptsummon.", ephemeral=True)
        return
    voice_channel = interaction.user.voice.channel
    bot_member = interaction.guild.me
    voice_permissions = voice_channel.permissions_for(bot_member) if bot_member else None
    if voice_permissions is None or not voice_permissions.connect or not voice_permissions.speak:
        await interaction.response.send_message(
            "I need both Connect and Speak permissions in that voice channel to listen and answer aloud.", ephemeral=True
        )
        return

    text_channel = interaction.channel
    if text_channel is None or not hasattr(text_channel, "send"):
        await interaction.response.send_message("Use /gptsummon from a text channel so I know where to send a fallback answer.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    try:
        await voice_assistant.join(interaction.guild, voice_channel, text_channel)
    except Exception:
        log.exception("Could not join voice channel in guild %s", interaction.guild.id)
        await interaction.followup.send("I couldn't join that voice channel. Make sure my role has Connect and Speak permissions there.", ephemeral=True)
        return
    await interaction.followup.send(
        f"Joined **{interaction.user.voice.channel.name}**. Say **AceGPT** or **Ace**, then your question. "
        "I'll answer aloud here. Speech recognition and speech synthesis run locally; only questions with the wake phrase are sent to the AI provider. "
        "The first question may take longer while the local speech model downloads.",
        ephemeral=True,
    )


@tree.command(name="gptdismiss", description="Send AceGPT out of the voice channel")
async def leave_voice(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        await interaction.response.send_message("Use this command in a server.", ephemeral=True)
        return
    if await voice_assistant.leave(interaction.guild.id):
        await interaction.response.send_message("I left the voice channel.", ephemeral=True)
    else:
        await interaction.response.send_message("I'm not listening in a voice channel right now.", ephemeral=True)


@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState) -> None:
    if bot.user is not None and member.id == bot.user.id and before.channel is not None and after.channel is None:
        await voice_assistant.handle_disconnect(member.guild.id)

@tree.command(name="reset", description="Clear AceGPT's conversation history in this channel")
async def reset(interaction: discord.Interaction) -> None:
    lock = channel_locks[interaction.channel_id]
    async with lock:
        histories[interaction.channel_id].clear()
    await interaction.response.send_message("Conversation history for this channel has been cleared.", ephemeral=True)


async def main() -> None:
    async with bot:
        await bot.start(DISCORD_TOKEN)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("AceGPT stopped")





