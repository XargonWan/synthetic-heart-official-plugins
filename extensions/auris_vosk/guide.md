# Vosk speech recognition

Local, offline speech-to-text with [Vosk](https://alphacephei.com/vosk/): it runs on the CPU,
needs no account and sends no audio anywhere.

## Use it

1. It is installed and on at the first start. It creates a hearing engine called **vosk**
   (Engines > Hearing).
2. Pick **vosk** as the active hearing engine. The model of your language (about 50 MB) is
   downloaded the first time it hears something, into the extension's own data directory.

## Settings

- **Language**: `en` (default), `it`, `fr`, `es`, `de`, `pt`, `zh`, `ja`, `ko`.
- **Model**: a specific model id (for example `vosk-en-us-large`, about 1.8 GB) instead of the
  small one of the language.

## Needs

`ffmpeg` on the machine, to turn any recording into the 16 kHz mono sound Vosk reads (the
SyntH image has it).

## What changed from the built-in engine

It is now an endpoint-backed engine like any other. The automatic language detection that
used the optional Whisper helper is gone: set the language. The active-engine name `vosk` is
the same, so an existing setup keeps working.
