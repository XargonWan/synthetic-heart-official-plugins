# SyntH official extensions

The official extension repository of [Synthetic Heart](https://github.com/xargonwan/synthetic_heart)
(SyntH). It is what the Plugins > Store of a SyntH installation reads, and what a fresh
installation installs its defaults from.

| Extension | What it does | Installed by default |
| --- | --- | --- |
| `official/synth_stage` | The SyntH Stage client (3D avatar, chat, voice) at `/stage/` | yes |
| `official/openai_api` | An OpenAI-compatible API (`/v1/chat/completions`, SSE streaming) | yes |
| `official/telegram_bot` | Telegram bot interface | yes |
| `official/fluxer` | Fluxer (Discord-compatible) interface | yes |
| `official/zen` | Registers the Zen LLM Engine endpoint | yes |
| `official/telegram` | Telegram **user account** (login from the WebUI, voice and video calls) | no (162 MB) |

## Use it

SyntH adds this repository by itself. To add it by hand (or on another installation):

```bash
python -m core.extensions.cli repo add official \
    https://github.com/XargonWan/synthetic-heart-official-plugins \
    --kind git --branch develop \
    --key "$(curl -s https://raw.githubusercontent.com/XargonWan/synthetic-heart-official-plugins/develop/public.key)" \
    --trusted
python -m core.extensions.cli install official/telegram --grant
```

Check the key against the fingerprint below before trusting it:

```
public key   cRgFJxeqnbfGN+CPSeXhINTQr7z7SxNCdYtzrNObXGY=
fingerprint  842730d171b5bef38249c0a1b76945e0
```

## What is in here

```
synth-repo.json        the index: every package with its SHA-256, size, platform and capabilities
synth-repo.json.sig    the Ed25519 signature over the exact bytes of the index
public.key             the publisher's public key
packages/official/     the packages (.synthx: a zip with the extension and its libraries inside)
publish.sh             rebuilds all of the above from the SyntH sources
```

A package is verified against the signed index before it is unpacked, and installing it
**grants nothing**: the operator grants what the manifest declares.

**Big packages.** `official/telegram` is 162 MB (the call library is 136 MB of native
code), more than a Git host accepts. The index lists it by URL, as an asset of the release
named in `publish.sh` (`RELEASE_TAG`); the SHA-256 and size in the signed index decide
whether the downloaded file is the right one, so the host does not have to be trusted.

**Platforms.** Packages that carry native libraries are built for one platform, and the
index says which (`linux-x86_64-py312` today). Another platform needs `publish.sh` run
there, which adds its packages to the index.

## Publishing

The extensions' sources are in the SyntH repository (`extensions_src/`) for now.

```bash
cd ~/gits/Synthetic_Heart && scripts/build_stage_extension.sh   # the Stage client
SYNTH_CORE=~/gits/Synthetic_Heart ./publish.sh
gh release create v0.1.0 dist/*.synthx --repo XargonWan/synthetic-heart-official-plugins
```

The signing key (`~/.config/synth/publisher.key`) is the one secret here: keep it out of
every repository and back it up. A lost or leaked key means every installation must be
told about a new one.

## Writing an extension

See [AGENTS.md](AGENTS.md): the rules an extension (and an agent writing one) must keep.
