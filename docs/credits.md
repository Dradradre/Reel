# Reel: credits

[← Back to the README](../README.md)

The optional relative-depth reader uses Depth Anything V2 Small. The DA-2K evaluation supplies annotated pairs of picture points. Both are distributed under Apache 2.0. [Depth Anything V2 Small](https://huggingface.co/depth-anything/Depth-Anything-V2-Small-hf) · [DA-2K](https://huggingface.co/datasets/depth-anything/DA-2K)

Anonymous voice embeddings use WeSpeaker’s VoxCeleb ResNet34 (Apache 2.0). The voice trial uses LibriSpeech test-clean recordings (CC BY 4.0). [WeSpeaker](https://huggingface.co/Wespeaker/wespeaker-voxceleb-resnet34) · [LibriSpeech](https://www.openslr.org/12/)

NVIDIA Audio Flamingo Next uses the NVIDIA OneWay Noncommercial License. The research setup downloads the audio.cpp Q4_K conversion and pinned macOS runtime separately. [NVIDIA model and terms](https://huggingface.co/nvidia/audio-flamingo-next-hf), [GGUF conversion](https://huggingface.co/audio-cpp/Audio-Flamingo-GGUF), [audio.cpp release](https://github.com/0xShug0/audio.cpp/releases/tag/v0.9.6)

The attack detector draws on Böck and Widmer’s frequency-neighbourhood method for suppressing vibrato. John Glover and colleagues provide the Modal recordings and hand annotations, with samples credited to the OLPC sound library. The environmental mixtures use Karol Piczak’s ESC-50 recordings (CC BY-NC 3.0). [Method](https://www.cp.jku.at/research/papers/Boeck_Widmer_DAFx_2013.pdf) · [Modal database](https://github.com/johnglover/modal) · [ESC-50 credits and licence](https://github.com/karolpiczak/ESC-50/blob/master/LICENSE)

<details>
<summary>Models, datasets, authors and licences</summary>

Sound location uses Meta’s Apache-2.0 PE-A-Frame Small. The timing comparison also tested Frame-MN from the MIT-licensed PretrainedSED repository and TUT Sound Events 2016 under its noncommercial scientific research agreement. [Meta model card](https://huggingface.co/facebook/pe-a-frame-small), [PretrainedSED](https://github.com/fschmid56/PretrainedSED) and [TUT 2016 evaluation data and terms](https://zenodo.org/records/996424)

The optional short-window classifier is Google’s Apache-2.0 YAMNet. The street recording check uses TUT Sound Events 2017, Evaluation, under its noncommercial research licence. [YAMNet model card](https://www.kaggle.com/models/google/yamnet/tfLite/classification-tflite/1) and [TUT recordings and terms](https://zenodo.org/records/1040179)

OVO-Bench by Niu et al. (CVPR 2025) provides the timed visual questions and their scorer. The benchmark uses CC BY-NC-SA 4.0 and the source-video datasets retain their own terms. [Project and dataset terms](https://github.com/JoeLeelyf/OVO-Bench#license)

The event timing check uses annotations from Shou et al. (Kinetics-GEBD, CC BY-NC 4.0). The clips are distributed separately by the Common Visual Data Foundation. [Annotations and licence](https://github.com/StanLei52/GEBD), [Kinetics recordings](https://github.com/cvdfoundation/kinetics-dataset).

The read-speech check uses the Bengali, English, Hindi, Korean, Latin American Spanish and Thai test sets from Google FLEURS (CC BY 4.0). [Recordings and reference transcripts](https://huggingface.co/datasets/google/fleurs).

The same-clip speech comparison uses the stimuli released by Magnotti et al. (2024) under CC0 and participant summaries from their deposited analysis. [Stimulus archive](https://openwetware.org/wiki/Beauchamp:Stimuli) and [authors’ analysis](https://zenodo.org/records/10689633).

A separate comparison used Meta’s Apache-2.0 XLSR phoneme recognizer. [Model card](https://huggingface.co/facebook/wav2vec2-xlsr-53-espeak-cv-ft) and [speech results](perception-check.md#같은-발화를-들은-사람-28명과-gpt).

The natural video comparison uses the human sound-only and audiovisual captions in AVCaps by Sudarsanam, Martín Morató, Hakala and Virtanen. We used the authors’ non-commercial research release (CC BY-NC 4.0). [AVCaps dataset card](https://huggingface.co/datasets/TUT-ARG/AVCaps) and [listening results](listening-check.md#사람들이-낯선-영상을-한-문장으로-설명한-경우)

The pitch check uses the 40 solo recordings and musician annotations released by Bittner, Pasalo, Bosch, Meseguer-Brocal and Rubinstein under CC BY 4.0. [vocadito recordings and annotations](https://zenodo.org/records/5578807)

The caption comparison in `examples/check_first_impressions.py` uses the sentence-transformers all-MiniLM-L6-v2 encoder under Apache-2.0. [Encoder and evaluation procedure](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2)

Reel stands on open work: [UNISAL](https://github.com/rdroste/unisal) (Apache-2.0) for gaze,
[Demucs](https://github.com/facebookresearch/demucs) (MIT) and
[audio-separator](https://github.com/nomadkaraoke/python-audio-separator) (MIT) with the Mel-Band Roformer crowd
model by aufr33 and viperx for separation, [Silero VAD](https://github.com/snakers4/silero-vad) (MIT) for speech activity and [Whisper](https://github.com/openai/whisper) (MIT) through
[mlx-whisper](https://github.com/ml-explore/mlx-examples) and [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
for speech, [ocrmac](https://github.com/straussmaximilian/ocrmac) for Apple Vision text, the
[Synchformer](https://github.com/v-iashin/Synchformer) (MIT) for picture-sound sync, the [AST AudioSet model](https://huggingface.co/MIT/ast-finetuned-audioset-10-10-0.4593) (BSD-3) for sound labels, librosa, OpenCV and PyTorch.

Optional sound comparison uses LAION's [CLAP HTSAT model](https://huggingface.co/laion/clap-htsat-unfused) (Apache 2.0).

Source identification uses the audio and text branches of Meta’s PE-AV Large checkpoint (Apache 2.0). Environmental source labels and recordings come from Karol Piczak’s ESC-50 collection (CC BY-NC 3.0). [Model](https://huggingface.co/facebook/pe-av-large) and [dataset and licences](https://github.com/karolpiczak/ESC-50)

The source library uses CC0 recordings from Freesound and the FSD50K annotations by Fonseca et al. (CC BY 4.0). Reel ships recording IDs and checksums. [FSD50K release, paper and licensing](https://zenodo.org/records/4060432)

Optional point tracking uses Google DeepMind’s online BootsTAPIR and its causal checkpoint, released under Apache-2.0. [Code and weights](https://github.com/google-deepmind/tapnet)

Optional sound separation uses Google Research’s MixIT checkpoint trained on YFCC100M. The authors release the checkpoint under Apache-2.0. [Model and license](https://github.com/google-research/sound-separation/tree/master/models/neurips2020_mixit#yfcc100m-model-checkpoint)

For the listening comparison we also tested Google’s Gemma 4 E4B-IT with the mlx-community 4-bit conversion. The original model card lists Apache-2.0. [Google model card](https://huggingface.co/google/gemma-4-E4B-it), [MLX conversion](https://huggingface.co/mlx-community/gemma-4-e4b-it-4bit)

Optional waveform descriptions use the Qwen team's Qwen3-Omni and the mlx-community 8-bit conversion. The model card lists Apache-2.0. [Original model](https://huggingface.co/Qwen/Qwen3-Omni-30B-A3B-Instruct), [MLX weights](https://huggingface.co/mlx-community/Qwen3-Omni-30B-A3B-Instruct-8bit)
Check each model's own licence before commercial use.

The demo images use the track "Crypto" by Kevin MacLeod (incompetech.com), licensed under Creative Commons
Attribution 4.0, and narration made with macOS `say`.

The speech check uses [ESC-10](https://github.com/karolpiczak/ESC-50) (CC BY 3.0) and the [Free Spoken Digit Dataset](https://github.com/Jakobovski/free-spoken-digit-dataset) (CC BY-SA 4.0).

The overlap check used recordings from all 50 categories of Karol J. Piczak’s ESC-50 dataset (CC BY-NC 3.0). [Dataset licence and individual recording credits](https://github.com/karolpiczak/ESC-50/blob/master/LICENSE)

The picture and sound checks use recordings, questions and visible sound annotations from Google DeepMind’s [Perception Test](https://github.com/google-deepmind/perception_test) (CC BY 4.0).
The full-frame and source-inspection comparison is recorded in [the perception check (Korean)](perception-check.md).

Reference captions come from the development split of Clotho v2.1 by Konstantinos Drossos, Samuel Lipping and Tuomas Virtanen, Audio Research Group at Tampere University. The university licenses the captions for experimental, noncommercial use. Setup keeps the full notice with the CSV and the local search index. [Dataset and license](https://zenodo.org/records/4783391)

First-listen descriptions use the MIT-licensed CoNeTTE model by Étienne Labbé, Thomas Pellegrini and Julien Pinquier. [Model and limitations](https://huggingface.co/Labbeti/conette) and [implementation](https://github.com/Labbeti/conette-audio-captioning)

The field listening check uses Clotho-AQA by Lipping, Sudarsanam, Drossos and Virtanen, and the captions from Clotho v2.1. The questions and answers use the MIT licence. Each Freesound recording has its own licence, and the captions use the Tampere University licence. [Clotho-AQA](https://zenodo.org/records/6473207) and [Clotho v2.1](https://zenodo.org/records/4783391)

The spoken grounding check uses the voice recordings, timed descriptions and pointer traces in Localized Narratives by Pont-Tuset, Uijlings, Changpinyo, Soricut and Ferrari (CC BY 4.0) with COCO validation photographs. [Localized Narratives](https://google.github.io/localized-narratives/) and [COCO terms](https://cocodataset.org/#termsofuse)

A local waveform experiment used Gemma 4 E2B IT. The base model card lists Apache 2.0 and the tested MLX conversion lists the Gemma licence. [Google model card](https://huggingface.co/google/gemma-4-E2B-it) and [MLX conversion](https://huggingface.co/mlx-community/gemma-4-e2b-it-4bit).

The sign-reading check uses human word transcriptions from COCO-Text V2 and COCO photographs. The source images retain their individual licences. [COCO-Text V2](https://bgshih.github.io/cocotext/) and [COCO image terms](https://cocodataset.org/dataset/termsofuse.htm)

The free-viewing check uses fixation data from the DIEM Project (Mital, Smith, Hill and Henderson), available for non-commercial research under CC BY-NC-SA 3.0. [DIEM data and licence](https://jhenderson.org/vclab/DIEM.html)

The headphone direction check uses the Two!Ears localization experiment by Wierstorf, Spors and Raake (CC BY 4.0). We rendered a fixed forward-facing head from their impulse responses and compared results with their listeners’ pointing responses. The screen localization comparison used the authors’ SSL-SaN weights and MIT implementation. [Two!Ears data and licence](https://docs.twoears.eu/en/latest/database/listening-tests/localisation/) and [SSL-SaN](https://github.com/xavijuanola/SSL_SaN)

The automatic localization experiment used the SLAVC authors’ audiovisual encoders trained on VGG-Sound 144k, with their Apache-2.0 implementation. [SLAVC paper, code and checkpoint](https://github.com/stoneMo/SLAVC)

The listening experiments also use MACS annotations from the Machine Listening Group at Tampere University and TAU Urban Acoustic Scenes 2019 recordings from the Audio Research Group at Tampere University of Technology, under their non-commercial experimental licences. [Listening results (Korean)](listening-check.md) · [MACS](https://zenodo.org/records/5114771) · [TAU recordings](https://zenodo.org/records/2589280)

</details>
