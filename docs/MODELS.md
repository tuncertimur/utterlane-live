# Models and voices

Utterlane Live ships no model weights. `utterlane setup` (run by the installer) downloads
exactly what your machine needs from the official sources, at pinned revisions, and checks
every file against a pinned SHA-256 before using it. After that, everything runs offline
except the brain you choose.

```bash
utterlane models status      # what this machine needs and what is present
utterlane models verify      # re-hash every file
utterlane models fetch --whisper small large-v3-turbo   # download extra STT models
```

Models are stored in the data folder (`utterlane doctor` prints it):

| OS | Data folder |
|---|---|
| Windows | `%LOCALAPPDATA%\Utterlane` |
| macOS | `~/Library/Application Support/Utterlane` |
| Linux | `$XDG_DATA_HOME/utterlane` (default `~/.local/share/utterlane`) |

Set `UTTERLANE_DATA_DIR` to move everything, or `UTTERLANE_MODELS_DIR` to move only the models.

## What runs where

| Job | Model | Size | Source (pinned) | License |
|---|---|---|---|---|
| Voice activity | Silero VAD v6.2.1 | 2 MB | [snakers4/silero-vad](https://github.com/snakers4/silero-vad) tag `v6.2.1` | MIT |
| Speaker embedding 1 | NVIDIA NeMo TitaNet-L (ONNX) | 101 MB | [k2-fsa/sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx/releases/tag/speaker-recongition-models) release | CC-BY-4.0 |
| Speaker embedding 2 | WeSpeaker ResNet34-LM, VoxCeleb (ONNX) | 27 MB | same release | CC-BY-4.0 |
| Speech-to-text | Whisper large-v3-turbo (CTranslate2) | 1.6 GB | [dropbox-dash/faster-whisper-large-v3-turbo](https://huggingface.co/dropbox-dash/faster-whisper-large-v3-turbo) | MIT |
| Speech-to-text (light) | Whisper small (CTranslate2) | 0.5 GB | [Systran/faster-whisper-small](https://huggingface.co/Systran/faster-whisper-small) | MIT |
| Voice, Turkish | Pocket TTS Turkish | 0.4 GB | [wite-tech/pocket-tts-turkish](https://huggingface.co/wite-tech/pocket-tts-turkish) | CC-BY-4.0 |
| Voice, English | Kyutai Pocket TTS | 0.25 GB | [kyutai/pocket-tts-without-voice-cloning](https://huggingface.co/kyutai/pocket-tts-without-voice-cloning) | CC-BY-4.0 |

Only one Whisper model is downloaded: the one chosen for your hardware.

## Speech-to-text choice

| Your machine | Model | Device | Precision | Beam |
|---|---|---|---|---|
| NVIDIA GPU (CUDA 12) | large-v3-turbo | GPU | int8_float16 | 5 |
| CPU with ≥ 16 threads | large-v3-turbo | CPU | int8 | 1 |
| Other CPUs (incl. Apple Silicon) | small | CPU | int8 | 1 |

Measured on Turkish ([FLEURS](https://huggingface.co/datasets/google/fleurs) test clips,
i9-10900X, RTX 3080):

| Model | Device | WER | Speed |
|---|---|---|---|
| large-v3-turbo | GPU | 0.112 | 0.34 s per turn |
| large-v3-turbo | CPU, 8 threads | 0.111 | real-time factor 0.61 |
| small | CPU, 8 threads | 0.274 | real-time factor 0.23 |

A real-time factor of 0.6 means a 5-second sentence takes about 3 seconds to transcribe.
Override the choice with `UTTERLANE_STT_MODEL` (`large-v3-turbo`, `small`),
`UTTERLANE_STT_DEVICE` (`cuda`, `cpu`), `UTTERLANE_STT_COMPUTE_TYPE`, `UTTERLANE_STT_BEAM_SIZE`
and `UTTERLANE_STT_CPU_THREADS`.
faster-whisper has no Apple GPU backend, so Macs use the CPU.

## Speaker identification

Each turn is embedded by both speaker models and the two cosine similarities are averaged;
both models are small enough for a CPU. Thresholds (`config.SpeakerThresholds`) were
calibrated on whole turns of consented Turkish recordings plus 150 public FLEURS-tr test clips:

| Setting | Value | Meaning |
|---|---|---|
| `match` | 0.48 | same person |
| `match_short` | 0.50 | same person, for turns under 1 s |
| `new_speaker` | 0.40 | below this, a new person |
| `margin` | 0.08 | best match must beat the runner-up by this much |
| `merge` | 0.60 | two "Person N" voices this similar are merged |

Between `new_speaker` and `match` the turn is labelled *Unclear* rather than guessed.

## Voices

Pick a voice with `--voice` or `UTTERLANE_TTS_VOICE`. The default is marked in bold.

**Turkish** (`--language tr`): **`female_1`**, `female_2`, `female_3`, `male_1`, `male_2`,
`male_3`. These are the six bundled voices of Pocket TTS Turkish (CC-BY-4.0).

**English** (`--language en`), from [kyutai/tts-voices](https://huggingface.co/kyutai/tts-voices):

| Voices | Source | License |
|---|---|---|
| **`alba`** | alba-mackenna | CC-BY-4.0 |
| `anna`, `azelma`, `charles`, `eponine`, `eve`, `fantine`, `george`, `jane`, `mary`, `michael`, `paul`, `vera` | VCTK | CC-BY-4.0 |
| `bill_boerst`, `caro_davy`, `peter_yearsley`, `stuart_bell` | voice-zero | CC0 |
| `javert`, `marius` | voice-donations | CC0 |
| `cosette` (Expresso), `jean` (EARS) | | **CC-BY-NC-4.0, non-commercial only** |

The app prints a warning when a non-commercial voice is selected. Voice cloning is not used:
only these built-in voice embeddings are loaded.

Pocket TTS can also speak German, French, Spanish, Italian, Portuguese and Dutch, but the
naming phrases ("I'm …", "… go ahead") are implemented for Turkish and English only, so the
app offers `tr` and `en`.

## Attribution

If you publish recordings made with the app, credit the voice: *"Voice: Pocket TTS Turkish by
wite-tech (CC-BY-4.0), based on Kyutai Pocket TTS"* or *"Voice: Kyutai Pocket TTS
(CC-BY-4.0)"*, plus the voice source above. Full list: [NOTICE](../NOTICE).
