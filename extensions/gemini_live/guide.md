# Gemini Live

Real-time voice conversations with [Gemini Live](https://ai.google.dev/gemini-api/docs/live):
the model hears the call, answers in its own voice and can use the SyntH's actions while it
talks. The SyntH keeps its persona, its history and its memory; this extension only holds the
connection to the model.

## Use it

1. Paste a **Gemini API key** (Google AI Studio) in the settings.
2. Choose **gemini_live** as the Live engine (Engines > Live).
3. Turn on **CALL_LIVE_ENGINE_ENABLED** (Calls). A call on an interface that can play audio
   as it arrives (a voice channel, for example) is then held by Gemini Live; any other call,
   or a call when the key is missing or the model is unreachable, stays on the classic path
   (speech recognition, a normal reply, the voice engine).

## Settings

- **Affective dialog** and **Proactive audio**: only the older 2.5 native-audio model has
  them, so turning either on switches to it.
- **Thinking level** (3.1) / **Thinking budget** (2.5).
- **Speech detection**: *manual* (default) tells the model when the caller starts and stops
  speaking, for sources that send nothing while the caller is silent; *server* lets the model
  decide, for sources that send continuous audio.

## Good to know

- A session lasts about ten minutes on Google's side. The extension reconnects by itself with
  Google's resumption handle, so the call does not notice. If a session cannot be resumed it
  is restarted with the last exchanges as context.
- What is said in a call is written to the chat history and to memory, and a diary entry is
  written when the call ends.
- The avatar's mouth does not move during a Live call.

## Needs

Network access to `generativelanguage.googleapis.com`.
