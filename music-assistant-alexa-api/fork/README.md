# Fork layer: Portuguese (pt-BR) voice features

This fork follows the upstream add-on (`timlaing/music-assistant-alexa-api`) closely. The
upstream skill is **never copied or edited**: the add-on image starts `FROM` upstream's signed
skill image, and everything specific to this fork is a thin layer on top.

```
upstream image (pinned by tag + digest in build.yaml / Dockerfile)   <- bumped, never edited
  └─ add-on glue (gunicorn, entrypoint, healthcheck)                  <- upstream files
      └─ fork layer (this directory)
           fork_ext/            Python package copied to /app/src/fork_ext
           models/pt-BR.intents.json   extra intents merged into upstream's pt-BR model
           apply_fork.py        two checked edits at build time (model + entrypoint)
           tests/               run inside the image, next to upstream's own suites
```

## What the layer does

| Piece | Purpose |
|---|---|
| `fork_ext/upstream_api.py` | **The only file that touches upstream internals.** Everything else imports these small functions. |
| `fork_ext/handlers.py` | pt-BR request handlers: dialogue on launch ("hora da música"), voice search, shortcuts, now playing, sleep timer, shuffle/repeat, help/fallback/error, pt-PT wording of upstream messages. |
| `fork_ext/voice_search.py` | Music Assistant search and playback (tracks, artists, radio, albums, playlists, podcasts, favourites, last radio, recent). |
| `fork_ext/ma_extras.py` | Now playing, shuffle/repeat, sleep timer. |
| `fork_ext/messages.py` | Every Portuguese sentence the skill speaks. |
| `fork_ext/wsgi.py` | Entry point used instead of `app:app`: installs the handlers, then loads upstream's app unchanged. |
| `models/pt-BR.intents.json` | Intents added to (or overriding the samples of) upstream's pt-BR model. Upstream's other intents are kept. |
| `apply_fork.py` | Runs during the image build. Merges the model and points the entrypoint at `fork_ext.wsgi:app`. **Fails the build with a clear message** if upstream's entrypoint changed shape. |

Handlers are registered ahead of upstream's (`PRIORITY_HANDLERS` before upstream's voice
handler because both define `PlayArtistIntent`, `PlayAlbumIntent`, `PlayPlaylistIntent` and
`PlayFavoritesIntent`; `REQUEST_HANDLERS` before upstream's launch handler). A test fails if a new
upstream intent name collides with one the fork handles.

## Taking a new upstream release

1. Merge `upstream/main` into this repository (`git fetch upstream && git merge upstream/main`).
   There should be **no conflicts**: the fork only adds files, plus the lines listed below.
2. Fork-owned edits to upstream files (expect conflicts only here):
   - `music-assistant-alexa-api/config.yaml`: `version`, `url`, `image` (ghcr.io/fleyd-1)
   - `music-assistant-alexa-api/Dockerfile`: the "Fork layer" block after `COPY rootfs /`
   - `music-assistant-alexa-api/CHANGELOG.md`: top entry
   - `.github/workflows/image.yaml`: image owner; `.github/workflows/builder.yaml`: `fork/*` path
   - `scripts/test_container.sh`: separate pytest run for `fork/tests`
3. Run the container tests (they run upstream's suites on the untouched skill **and** the fork's):
   ```
   git clone --depth 1 --branch vX.Y.Z https://github.com/timlaing/music-assistant-alexa-skill.git \
       music-assistant-alexa-api/skill-api
   docker build -t ma-alexa:test music-assistant-alexa-api
   bash scripts/test_container.sh ma-alexa:test linux/amd64
   ```
4. If something fails, `fork/tests/test_contract.py` names the seam that moved. Fix it in
   `fork_ext/upstream_api.py` (or `apply_fork.py` if the entrypoint changed) and nowhere else.
5. Bump `config.yaml` `version`, add a `CHANGELOG.md` entry, open a PR, merge when CI is green.
6. Back up Home Assistant, update the add-on, then in the add-on's web page run
   **Review and Deploy** so Amazon gets the (possibly changed) interaction model.

## Voice commands

See the table in the repository wiki / Notion page "Alexa + Music Assistant: comandos de voz".

## Staying informed

`fork/UPSTREAM_BASE` records which upstream add-on release this fork is built on. The
`Upstream watch` workflow checks every Monday and opens an issue when upstream publishes a newer
release. Update `UPSTREAM_BASE` when you finish an upgrade.
