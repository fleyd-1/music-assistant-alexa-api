## 1.4.1

- Match artist names with the Portuguese Alexa artist slot and resolve exact local artists before global searches.
- Queue a bounded set of artist tracks and remove the synchronous stream URL probe from the Alexa response path.
- Add request-correlated timing logs for Music Assistant search, queue preparation, stream push and Alexa playback events.

## 1.3.9

- Add one-sentence Portuguese requests to play an artist through the Music Assistant Alexa skill.

## 1.3.8

- Normalize repeated "radio" prefixes emitted by Alexa routines before searching saved stations.

## 1.3.7

- Normalize spoken radio names before searching saved stations, avoiding provider false matches and timeouts.
- Route the stale saved FUTURA station to its verified official Radio.co stream.

## 1.3.6

- Ask what to play when a previously cached stream has expired, instead of ending the launch with a misleading connection error.
- Keep private stream URLs out of validation logs.

## 1.3.5

- Route both flow and single/radio streams to the correct Echo.
- Search saved media before online providers and allow a bounded 6.5-second search/start budget.

## 1.3.4

- Align Portuguese invocation and playback samples with the Music Assistant provider.
- Include infinitive search utterances and next/previous intents.
- Verify exported icon bytes when Amazon rewrites icon references.

## 1.3.3

- Keep an empty launch session open to request a track or station.
- Search title and artist separately with metadata verification; prefer saved stations.
- Isolate cached streams by Music Assistant player to prevent cross-device playback.
- Include voice-search regression tests in container CI.

<!-- https://developers.home-assistant.io/docs/add-ons/presentation#keeping-a-changelog -->

## 1.3.2

- Remove broad no-slot playback phrases that caused Alexa to route specific track and radio requests to `PlayAudio` instead of the voice-search intents.

## 1.3.0

- Promote the personal-skill deployment wizard to stable following maintainer confirmation that it is tested and working.

- Put LWA registration instructions, the developer-console link and generated callback URL in Connect to Amazon.
- Show connection success with a green tick and place setup feedback in its relevant section.
- Offer Trusted, Trusted sub-domain and File certificate choices; validate and deploy public PEM certificates for development testing.
- Use the maintained add-on artwork for the skill icons.
- Wait for running builds and verify the imported package when Amazon omits legacy build-status fields, preserving failure/mismatch checks and accepted-import resume.

## 1.3.0-beta.3

- Show Amazon connection progress and errors beside Connect, validate settings before sign-in and provide a direct sign-in link when pop-ups are blocked.
- Share responsive page styling between Status and Setup; place Setup navigation above the status title and group checks under section headings.
- Put required API username/password in Credentials and additional connection fields in Advanced settings.
- Default the skill endpoint to the public audio URL origin plus `/ma-alexa-skill/`, with an optional override.

## 1.3.0-beta.2

- Fix Home Assistant Open Web UI opening the status page with a doubled slash; accept cached status entry links only through the trusted ingress gateway.

## 1.3.0-beta.1 (candidate)

- Move application settings into the ingress setup page, migrate available legacy values and persist web edits.
- Generate the callback URL and initial API password; mask secrets and provide deliberate provider-password reveal.
- Remove the unused AWS region configuration.

- Provide status/setup through Home Assistant ingress without a separate app login, preserving API authentication.
- Keep the Amazon callback under `/ma-alexa-skill/`, completing sign-in in the original ingress browser.
- Verify configured URL reachability on status with background HTTPS/health/audio checks.

- Replace interactive web ASK CLI setup with self-hosted Amazon browser sign-in and direct management APIs.
- Add explicit existing-skill selection, configuration review and supported package import/build verification.
- Preserve skill identity, other locales and unrelated resources; prevent automatic deletion and duplicate creation retries.
- Persist private credentials/progress and resume accepted imports after restart.
- Add Login with Amazon and certificate options, callback proxy guidance and automated protocol/security tests.
- Live Amazon deployment and Echo acceptance are still required before promotion.

## 1.2.0

- Promote the tested beta after maintainer confirmation of real-device playback through NPM.
- Verify signed base image indexes before publication and in PR builds.
- Document shared-host `/alexa/` and `/ma/` API proxy routing.

## 1.2.0-beta.1

- Update to latest upstream via the maintained Alexa skill fork.
- Fix single-worker playback deadlocks with one threaded worker and direct store access.
- Correct HTTPS stream/artwork rewriting for NPM hostnames and path prefixes.
- Stop stale flow streams and announcements from being enqueued repeatedly.
- Add optional MA voice-control credentials, persistent device mapping and APL settings.
- Add idle-aware status, unauthenticated health checks and fault diagnostics.
- Pin base images and Python dependencies; validate both architecture images in CI.

## 1.1.0

- Update the Alexa skill submodule and support its refactored application layout.
- Add the upstream skill setup UI, locale selection, and optional stream URL validation bypass.
- Run a single application worker so pushed stream state remains consistent.

## 1.0.3

Fix gunicorn worker issues

## 1.0.2

Bugfix environment variable

## 1.0.1

Refactor docker / repo dependencies

## 1.0.0

- Initial release
