# SyntH official extensions

The official extension repository of [Synthetic Heart](https://github.com/xargonwan/synthetic_heart)
(SyntH): a **catalog** of extensions, each published as its own `.zip` asset of a
**rolling GitHub release**. It is what the Plugins > Store of a SyntH installation reads,
and what a fresh installation installs its defaults from.

| Extension | What it does | Installed by default |
| --- | --- | --- |
| `official/synth_stage` | The SyntH Stage client (3D avatar, chat, voice) at `/stage/` | yes |
| `official/synth_dashboard` | The Control Deck (Dashboard): every setting as a knob, toggle or readout, in the More… menu | yes |
| `official/openai_api` | An OpenAI-compatible API (`/v1/chat/completions`, SSE streaming) | yes |
| `official/telegram_bot` | Telegram bot interface | yes |
| `official/fluxer` | Fluxer (Discord-compatible) interface | yes |
| `official/zen` | Registers the Zen LLM Engine endpoint | yes |
| `official/telegram` | Telegram **user account** (login from the WebUI, voice and video calls) | no (162 MB) |

## Releases

There is no release per update. Each branch has **one rolling release** whose tag moves to
the newest commit and whose assets are replaced:

| Branch | Release tag | |
| --- | --- | --- |
| `develop` | `latest-develop` | pre-release |
| `main` | `latest` | |

A release holds:

```
official-<name>.zip    one extension (a zip with its code and libraries; the version is in its manifest)
synth-repo.json        the catalog: every extension with its version, SHA-256, size, platform, capabilities
synth-repo.json.sig    the Ed25519 signature over the exact bytes of the catalog
```

The version of an extension is its own (in `synth-extension.toml` inside the zip and in the
catalog); the release does not carry one.

## Use it

SyntH adds this repository by itself the first time its extension runtime starts. By hand:

```bash
python -m core.extensions.cli repo add official github:XargonWan/synthetic-heart-official-plugins \
    --kind github --branch latest-develop --trusted \
    --key "$(curl -sL https://raw.githubusercontent.com/XargonWan/synthetic-heart-official-plugins/develop/public.key)"
python -m core.extensions.cli install official/telegram --grant
```

(`--branch` is the release tag: `latest` for the `main` channel.) Check the key against the
fingerprint before trusting it:

```
public key   cRgFJxeqnbfGN+CPSeXhINTQr7z7SxNCdYtzrNObXGY=
fingerprint  842730d171b5bef38249c0a1b76945e0
```

A package is verified against the signed catalog before it is unpacked, so GitHub does not
have to be trusted; and installing it **grants nothing**: the operator grants what the
manifest declares.

**Platforms.** Packages that carry native libraries are built for one platform, and the
catalog says which (`linux-x86_64-py312`, the platform of SyntH's image).

## The pipeline

`.github/workflows/publish.yml` builds and publishes on every push to `develop` or `main`
(and on `workflow_dispatch` or a `core-updated` repository dispatch). A pull request only
builds, unsigned.

1. It checks out the SyntH repository (`XargonWan/synthetic_heart`): the extensions'
   sources are `extensions_src/` there for now, and the packer is `core/extensions`.
2. `scripts/build_release.sh` builds the Stage client, packs every extension with its
   libraries and writes the signed catalog.
3. `scripts/verify_release.py` refuses to go on unless the catalog verifies with this
   repository's `public.key` and every file matches it.
4. It moves the rolling tag to the commit, creates or updates the release, uploads the
   assets (replacing the old ones, deleting those no longer built).

**Set up once** (repository settings):

| What | Value |
| --- | --- |
| Secret `SYNTH_PUBLISHER_KEY` | the publisher key file, base64: `base64 -w0 ~/.config/synth/publisher.key` |
| Variable `SYNTH_CORE_REF` (optional) | the SyntH ref to build from, for example `refactor/plugin-system` until it is merged; default `develop` / `main` |

The publisher key is the one secret here: keep it out of every repository and back it up. A
lost or leaked key means every installation must be told about a new one.

Locally: `SYNTH_CORE=~/gits/Synthetic_Heart scripts/build_release.sh` writes `dist/`.

## Writing an extension

See [AGENTS.md](AGENTS.md): the rules an extension (and an agent writing one) must keep.
