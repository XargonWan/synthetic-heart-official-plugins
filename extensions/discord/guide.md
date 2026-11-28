# Discord Bot: setup

1. In the [Discord Developer Portal](https://discord.com/developers/applications) create an
   application, add a **Bot**, and press **Reset Token** to copy it.
2. On the same Bot tab, switch on **MESSAGE CONTENT INTENT** (Privileged Gateway Intents).
   Without it the bot cannot read what people write.
3. Paste the token into **Bot token** here and save.
4. Invite the bot: OAuth2 > URL Generator, scope `bot`, with at least *Send Messages* and
   *Read Message History*.

Mention the bot, reply to it, or write to it directly to talk to it. Set your Discord user id
as trainer in the settings to give yourself the trainer's powers.

**Voice.** Ask it to join while you are in a voice channel (the model uses
`join_voice_discord`); it listens, answers aloud and leaves with `/leave` or when everybody
has left. By default only the trainer can ask; change that in the settings.
