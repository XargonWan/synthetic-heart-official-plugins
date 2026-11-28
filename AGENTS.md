# AGENTS.md: writing a SyntH extension

For anyone, human or agent, who writes or changes an extension in this repository. The
platform's documentation is in the SyntH repository, `docs/plugin_platform/` (start with
`extension_developer_guide.rst` and `extension_runtime.rst`). **When this file and that
documentation disagree, the documentation wins.** When the SyntH checkout is next to this
repository (`../Synthetic_Heart`), read it there; the SDK you call is `synth_sdk/` in it.

## What an extension is

A separate process (`python -S -I`, a clean environment, a data directory of its own) that
talks to SyntH only through a JSON-RPC gateway, using the `synth_sdk` it is started with.
Its package is one `.zip`: a manifest (`synth-extension.toml`), code, assets and the
libraries it needs, built into `lib/`.

## Rules (not negotiable)

1. **Never import SyntH.** No `core.*`, `plugins.*`, `interface.*`, `engines.*`: only the
   standard library, `synth_sdk` and the libraries you bundle.
2. **No shortcut.** If the API lacks what you need, **ask the maintainers for a new API**
   (open an issue on the SyntH repository describing the capability); do not read the
   database, the `.env`, another extension's files or the SyntH process.
3. **Do not bypass the message chain.** Deliver what you receive with
   `ctx.interfaces.inbound`; send what the Core hands you from your `send_message` handler.
   History, aliases, commands and the queue are the Core's.
4. **Ask for the least.** Declare only the capabilities you use, in the manifest; the
   operator grants them. A call without both is refused.
5. **No dependency on another extension's code.** If you need one, declare it in
   `[[requires.extensions]]` with a version range and talk to it through the gateway.
6. **Bring your libraries.** Anything SyntH does not provide goes in `[python] requirements`
   and is bundled into `lib/` when the package is built.
7. **No secrets in logs or storage you do not need.** Mark secret settings `sensitive = true`.
   Never write a token or a login code to the log.
8. **No word lists.** Do not detect intent or language with keyword lists; return structured
   values and let the model decide.
9. **Never block.** Every handler is `async`; a slow call must not stall the event loop.
10. **Never commit a private key** (`*.key`) or built packages (`dist/`).

## Layout of an extension

```
extensions/<name>/
  synth-extension.toml   id, version, [compat], [entry], [permissions], [config.*]
  ext_main.py            async def main(ctx): runs until ctx.stopping is set
  convert.py             pure functions (parse, shape): the part worth unit tests
  guide.md               setup guide shown in the WebUI
  icon.svg
```

`extensions/hello/` is the smallest working example. For larger ones, read the official
extensions: <https://github.com/XargonWan/synthetic-heart-official-plugins> (`fluxer` is an
interface, `openai_api` a request/response interface with a web server, `zen` registers an
endpoint, `synth_stage` is a static page, `skin_riko` is a skin).

## Tests

Test against a **real extension process** behind the Core's real gateway, and a **fake of the
outside service** (a fake chat server, a fake client library). `ext_tests/test_extension_hello.py`
is the pattern. `scripts/test.sh` runs them. **Check a test can fail**: break the code and
watch it go red. `ext_tests/test_conformance.py` checks the manifest and the import rule for
every extension.

## Before you finish

1. `scripts/test.sh` is green.
2. `scripts/local_install.sh` installs your package from a local repository.
3. `version` is bumped in the manifest for any change that ships: an installed version is
   never replaced by the same number.
4. `experimental = true` while it is unfinished.
