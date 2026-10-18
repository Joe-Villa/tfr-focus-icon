# TFR Focus Icon Generator

English | [中文](README.md)

Focus icons in *The Fire Rises* (TFR) style, for TFR and its submods: **hand over a focus name and a
one-line description, get back a PNG that drops straight into your mod.**

## Why this exists

TFR's focus icons are one of its best-known faces: roughly 175×150 px, transparent background,
irregular silhouette, **photobashed composite**, diagonally stacked elements, dark outline with a soft
outer glow. Making a good one is real work: sourcing material, cutting, layering, grading, then
resizing, outlining, adding a slogan and wiring it into the mod. Multiply that by a whole focus tree
and it becomes a chore.

This project hands that pipeline to an AI agent: **a request goes in, a finished icon comes out**, no
human babysitting in between.

The visual style is not a matter of taste — it is **calibrated numerically against real TFR artwork**.
Hundreds of TFR icons were measured (canvas size, ink coverage, silhouette solidity, soft-edge ratio,
outline width, slogan size) and every generated icon has to pass two numeric gates (statistical
alignment plus a hard junk check) before it counts as usable. "Looks like TFR" and "measures like TFR"
are the same thing here.

**The defaults are tuned for TFR.** Other styles (vanilla HOI4's flat icons, other mods' art) are not
out of reach, but they need recalibration: measure your own reference set with
`scripts/metrics.py --stats --mod <your icons dir>`, then adjust `palette / treatments / tight_target`
in the design JSON. TFR is the one style that works out of the box.

## Cost

**You need a cloud image-generation API** (no local diffusion model, no GPU). The reference
implementation uses SiliconFlow and requires your own API key.

| Item | Measured |
|---|---|
| Image price | ¥0.30 per image (official pricing page) |
| One delivered icon | **≈ ¥1.8** — one round of 6 candidates, pick 1 |
| End-to-end time | ~1 minute (6 candidates + post-processing) |
| Cutout / outline / slogan / final sizing | done locally on CPU, **free** |

Cost is essentially "images generated × unit price". You can lower the candidate count to save money,
but you also lose the selection. A diffusion model produces a different composition every call, so
generating a batch and picking the best usually beats iterating on a single image. Every API call is
logged, so the bill is always reproducible (see `scripts/cost.py`).

## Limitations

**To be explicit: this is something that has been *built*, not something that is already *polished*.**

- **The "people" it draws are lookalikes, not real likenesses.** Real mod icons often use photographs
  and historical footage; that is out of reach here. If you need a specific historical figure, accept
  this first.
- **Layering is weaker than hand-made work.** A single generated frame lacks the depth of several
  cut-outs stacked diagonally; getting that look means the element-collage route, which doubles cost
  and needs more hands-on layout.
- **The model sometimes paints a ground reflection** under the subject, which survives as a pale grey
  band after keying. Negative prompts reduce it but do not eliminate it — the workaround is more
  candidates and picking a clean one.
- **Content moderation is random** — the same prompt passes or fails on different attempts. More
  importantly, unsuitable subject matter simply cannot be made: real political figures and real
  military subjects are out of scope, and retrying will not fix that.
- **Verified on one machine only.** There is a cost-free smoke self-test (`selftest.sh`, 10 checks),
  but **no real test suite**, and it has never been run in a clean environment.
- **Fine detail is unstable**: small text turns into texture at 150 px, and the slogan is composited
  locally (crisp and editable, but visibly "stuck on").

## How to use it

Install it as a skill for Codex (or any agent that can read `SKILL.md`), then **just describe what you
want in one sentence** — focus name, country, what the picture should say. The agent takes over from
there: prompts, candidates, cutout, acceptance checks, mod installation.

No step-by-step instructions here, because the user is an agent, not a person.

## Delivery budget and stop conditions

The agent no longer refines without a bound: by default it generates one round of 6 candidates,
allows at most 2 local refinements and 8 image views, and stops at 20 minutes (30 minutes for
element collage / image editing). Once both `qa_gate` and `metrics.py --strict` pass, it must
deliver; when the budget runs out, it must deliver the best current candidate and report what
is still missing. See [docs/stop-conditions.md](docs/stop-conditions.md) for the full rules
(in Chinese) and verification commands.

## What's in the repo

| File | Content |
|---|---|
| `SKILL.md` | The workflow and hard rules the agent reads (the entry point) |
| `docs/stop-conditions.md` | Delivery budget, stop conditions, and the `metrics.py --strict` update |
| `references/` | Details: style card and numeric baselines, collage composition, pitfalls and troubleshooting, mod integration, sub-agent split, visual self-review |
| `scripts/` | Ready-to-call tools: generation driver, API client, cutout, post-processing, objective metrics, junk gate, cost ledger, reference-image gate, reference contact sheet |
| `install.sh` | Installs this repo into your Codex skills directory (`$CODEX_HOME/skills/`) |
| `selftest.sh` | Smoke self-test: compilation, the image gate (bad files must be blocked), metrics, ledger math — **offline, no API spend** |
| `requirements.txt` | Runtime dependencies (Pillow / numpy / scipy / onnxruntime); a CJK font is also needed |

**This repository ships no reference images**: a real mod's icons are someone else's work and are not
redistributed here. When a style baseline is needed, the agent points at your own HOI4 installation.

## License

MIT — see [LICENSE](LICENSE).
