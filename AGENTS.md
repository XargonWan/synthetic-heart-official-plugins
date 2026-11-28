# AGENTS.md: writing a SyntH extension

For anyone, human or agent, who writes or changes an extension. The platform's own
documentation is in the SyntH repository: `docs/plugin_platform/` (start with
`extension_developer_guide.rst`, `extension_runtime.rst` and `manifest_proposal.rst`).
When this file and the platform's documentation disagree, the documentation wins.

## What an extension is

A separate process (`python -S -I`, a clean environment, a data directory of its own) that
talks to SyntH only through a JSON-RPC gateway, using the `synth_sdk` it is started with.
Its package is one `.synthx` file: a manifest (`synth-extension.toml`), code, assets and the
libraries it needs, built into `lib/`.

## Rules (not negotiable)

1. **Never import SyntH.** No `core.*`, `plugins.*`, `interface.*`, `engines.*`: only the
   standard library, `synth_sdk` and the libraries you bundle. `rg "from core|import core"`
   in your extension must find nothing.
2. **No shortcut.** If the API lacks what you need, **ask the maintainers for a new API**;
   do not read the database, the `.env`, another extension's files or the SyntH process.
   An official extension uses the same API as a third-party one.
3. **Do not bypass the message chain.** Deliver what you receive with `ctx.interfaces.inbound`;
   send what the Core hands you from `on_send_message`. History, aliases, commands and the
   queue are the Core's.
4. **Ask for the least.** Declare only the capabilities you use. They are declared in the
   manifest and granted by the operator; a call without both is refused. `web.origin`
   (running pages as the person) is the dangerous one: do not ask for it unless the page
   truly needs the WebUI session.
5. **No dependency on another extension's code.** If you need one, declare it in
   `[[requires.extensions]]` with a version range, and talk to it through the gateway.
6. **Bring your libraries.** Anything SyntH does not provide goes in `[python] requirements`
   and is bundled into `lib/` when the package is built. Do not assume the host has it.
8. **No secrets in logs or storage you do not need.** Mark secret settings `sensitive = true`.
   Never write a token or a login code to the log.
9. **No word lists.** Do not detect intent or language with keyword lists; return structured
   values and let the model decide.
10. **Never block.** Every handler is `async`; a slow call must not stall the event loop.

## Layout

```
synth-extension.toml   id, version, [compat] core_api, [entry], [provides], [permissions], [config.*]
ext_main.py            async def main(ctx: Context): runs until ctx.stopping is set
convert.py             pure functions (parse, shape): the part worth unit tests
guide.md               setup guide shown in the WebUI
icon.svg
```

Look at `official/fluxer` (an interface), `official/openai_api` (a request/response
interface with a web server), `official/zen` (registers an endpoint) and
`official/synth_stage` (a static page) in `extensions/` here.

## Tests

Test against a real process and a **fake of the outside service** (a fake chat server, a
fake client library), driving the extension through the gateway; `ext_tests/` shows how.
They run against a SyntH checkout (`SYNTH_CORE`), whose gateway and helpers they use. Check a test can fail: break the code and watch it go
red.

## Publishing

The pipeline (`.github/workflows/publish.yml`) builds every extension and publishes each as
`official-<name>.zip` in the rolling release of its branch (`latest-develop` for `develop`,
`latest` for `main`), with the signed catalog. Bump `version` in the manifest for any change
that ships: an installed version is never replaced by the same number. Do not commit built
packages or catalogs here, and never the publisher key.
