# My SyntH extensions

A repository of extensions for [Synthetic Heart](https://github.com/xargonwan/synthetic_heart)
(SyntH), started from the official template. Each extension in `extensions/` is published as
its own `.zip` asset of a **rolling GitHub release**, together with a signed catalog that
SyntH can add as a repository.

```
extensions/hello/          a minimal working extension: copy it to start yours
ext_tests/                 tests: a real extension process behind the Core's real gateway
scripts/test.sh            run the tests
scripts/local_install.sh   build a local repository and install from it, as a person would
scripts/build_release.sh   build dist/: one zip per extension + the catalog
.github/workflows/         builds and publishes the release on every push
AGENTS.md                  the rules; point your coding agent at it
```

## 1. Start from this template

The template is the `template` branch of
[synthetic-heart-official-plugins](https://github.com/XargonWan/synthetic-heart-official-plugins).
It has no history of its own, so your repository starts clean.

```bash
git clone --branch template --single-branch \
    https://github.com/XargonWan/synthetic-heart-official-plugins.git my-synth-extensions
cd my-synth-extensions
rm -rf .git && git init -b main && git add . && git commit -m "chore: start from the SyntH extension template"
git remote add origin git@github.com:YOU/my-synth-extensions.git && git push -u origin main
```

(Or fork the repository on GitHub and keep only the `template` branch; either way, your
`main` is the template.)

You also need a **SyntH checkout** next to it: the packer, the gateway and the SDK your
tests run against live there.

```bash
git clone https://github.com/XargonWan/synthetic_heart.git ../Synthetic_Heart
(cd ../Synthetic_Heart && uv sync)          # once
```

(Another location: `export SYNTH_CORE=/path/to/synthetic_heart`.)

## 2. Write your extension

Rename `extensions/hello/` and set your own `id = "yourname/<name>"` in its manifest. Then
either write it yourself (`extensions/hello/` is commented) or ask a coding agent.

### A prompt for your coding agent

Open the agent in this repository (it reads `AGENTS.md` by itself; if yours does not, name
it) and give it something like:

> Read `AGENTS.md` and the SyntH platform documentation in `../Synthetic_Heart/docs/plugin_platform/`
> (`extension_developer_guide.rst`, `extension_runtime.rst`) and the SDK in
> `../Synthetic_Heart/synth_sdk/runtime.py`. Then create a new SyntH extension
> `yourname/<name>` in `extensions/<name>/`, starting from `extensions/hello/`.
>
> **What it should do:** <describe it: what service or device it connects to, what a person
> sees in SyntH, which settings it needs (and which are secret), which messages or events it
> sends or receives>.
>
> **Rules:** follow `AGENTS.md` to the letter. Never import `core`, `plugins`, `interface` or
> `engines`; use only `synth_sdk` and the libraries you bundle in `[python] requirements`.
> Declare only the capabilities you really use. If the API cannot do what is needed, stop and
> tell me which API is missing instead of working around it.
>
> **Deliver:** the manifest (id, version `0.1.0`, `experimental = true`, `[config.*]` for each
> setting, `sensitive = true` for secrets), `ext_main.py`, the pure parts in `convert.py`,
> a `guide.md` for the person installing it, an `icon.svg`, and tests in `ext_tests/`: a real
> extension process behind the real gateway and a **fake of the outside service**. Run
> `scripts/test.sh` until it is green, prove at least one test can fail by breaking the code,
> then run `scripts/local_install.sh`. Report what you could not verify.

The more concrete the "what it should do" paragraph, the better the result. Name the
service's documentation or API when there is one, and say which of the SDK's namespaces
(`ctx.interfaces`, `ctx.endpoints`, `ctx.web`, `ctx.avatar`, ...) you expect it to use.

## 3. Test it locally

Three steps, from fastest to most realistic. None needs the repository to be published.

**a) Run the tests.** The extension runs as a real process behind the Core's real gateway,
with in-memory stand-ins for storage and config:

```bash
scripts/test.sh               # all of them
scripts/test.sh -k hello -x   # pytest arguments go through
```

**b) Install it from a local repository** into a throwaway runtime root
(`.local-runtime/`), as a person would. It proves the package builds, the catalog is valid
and the installer accepts it:

```bash
scripts/local_install.sh                  # every extension in extensions/
scripts/local_install.sh yourname/hello   # one of them
```

**c) Try it in a running SyntH.** Build the repository, then add its folder as a **local
repository**:

```bash
scripts/build_release.sh      # writes dist/ (unsigned unless you have a publisher key)
```

*SyntH from a checkout* (not Docker): in the WebUI, **Plugins > Repositories**, add the
absolute path of `dist/` (the kind is detected as local), leave the public key empty
(unsigned) and tick **Trusted**. Or from a terminal:

```bash
cd ../Synthetic_Heart
uv run python -m core.extensions.cli repo add mine /abs/path/to/my-synth-extensions/dist --kind local --unsigned --trusted
uv run python -m core.extensions.cli install yourname/hello --grant
```

*SyntH in Docker*: the path must exist inside the container. Mount `dist/` read-only with a
`docker-compose.override.yml` next to SyntH's compose file:

```yaml
services:
  synth:
    volumes:
      - /abs/path/to/my-synth-extensions/dist:/local-repo:ro
```

then `docker compose up -d` and add `/local-repo` as the repository's location.

Then **Plugins > Catalog > your extension > Install**, and **grant** what its manifest
declares. Its settings appear in the extension's page; its log lines are tagged with its id
in **Logs**.

**The edit loop.** Change the code, **bump `version` in the manifest** (an installed version
is never replaced by the same number), run `scripts/build_release.sh`, then **Update** in
the Catalog (or `... cli update yourname/hello`). The installer keeps the previous version:
**Roll back** is one click (or `cli rollback`).

**If something goes wrong:** the extension's stderr and log lines go to SyntH's log
(`get_recent_errors`, or **Logs** in the WebUI); a crash is restarted with a back-off and
shown as such in the extension's state. A call refused with `PermissionDenied` means the
capability is missing from the manifest or was not granted.

## 4. Publish

Once the extension is worth sharing:

1. Make a publisher key (once): `cd ../Synthetic_Heart && uv run python -m core.extensions.cli keygen ~/.config/synth/publisher.key`.
   Keep it out of every repository and back it up. Copy the public key it prints into
   `public.key` in this repository and commit it.
2. In the repository's GitHub settings add the **secret** `SYNTH_PUBLISHER_KEY`:
   `base64 -w0 ~/.config/synth/publisher.key`.
3. Push to `main`. The workflow runs the tests, builds `dist/`, checks the catalog verifies
   with `public.key`, and moves the rolling release `latest` to the commit (`develop` goes to
   `latest-develop`, a pre-release).
4. Anyone adds your catalog in **Plugins > Repositories**:
   `github:YOU/my-synth-extensions`, kind `github`, release tag `latest`, and your public key.
   Installing a package grants nothing; the person grants what the manifest declares.

The workflow builds SyntH's packer from `XargonWan/synthetic_heart`. While a SyntH change you
depend on is not merged, set the repository variable `SYNTH_CORE_REF` to the branch to use.
