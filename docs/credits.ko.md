# Reel에서 사용한 오픈소스

[← README로 돌아가기](../README.ko.md)

화면의 상대 깊이는 Depth Anything V2 Small로 추정한다. DA-2K의 두 지점 중 카메라에 더 가까운 곳을 표시한 자료로 결과를 확인했다. 둘 다 Apache 2.0을 따른다. [Depth Anything V2 Small](https://huggingface.co/depth-anything/Depth-Anything-V2-Small-hf) · [DA-2K](https://huggingface.co/datasets/depth-anything/DA-2K)

재생 중 목소리를 비교할 때 WeSpeaker VoxCeleb ResNet34(Apache 2.0)를 쓴다. 화자 시험에는 LibriSpeech test-clean 녹음(CC BY 4.0)을 사용했다. [WeSpeaker](https://huggingface.co/Wespeaker/wespeaker-voxceleb-resnet34) · [LibriSpeech](https://www.openslr.org/12/)

소리가 시작되는 시각을 잴 때는 뵈크와 비트머가 비브라토를 거르는 데 쓴 방법을 참고했다. 악기 소리는 존 글러버 연구진이 OLPC 음원으로 만든 Modal 녹음과 수작업 주석으로 검증했다. 생활음은 카롤 피차크가 공개한 ESC-50 녹음(CC BY-NC 3.0)을 썼다. [검출 방법](https://www.cp.jku.at/research/papers/Boeck_Widmer_DAFx_2013.pdf), [Modal 자료](https://github.com/johnglover/modal), [ESC-50 출처와 이용 조건](https://github.com/karolpiczak/ESC-50/blob/master/LICENSE)

<details>
<summary>모델과 데이터 출처, 만든 사람, 이용 조건</summary>

다시 들을 시각을 찾는 모델은 Meta가 Apache 2.0으로 공개한 PE-A-Frame Small이다. 비교 실험에는 MIT 라이선스의 PretrainedSED에 포함된 Frame-MN과 탐페레 공과대학교가 비상업 과학 연구용으로 배포한 TUT Sound Events 2016 평가 녹음을 썼다. [Meta 모델 카드](https://huggingface.co/facebook/pe-a-frame-small), [PretrainedSED](https://github.com/fschmid56/PretrainedSED), [TUT 2016 녹음과 이용 조건](https://zenodo.org/records/996424)

짧은 소리 분류에는 Google의 Apache 2.0 모델 YAMNet을 쓴다. 거리 녹음 검증에는 탐페레 공과대학교의 TUT Sound Events 2017 평가 자료를 비상업 연구용 라이선스로 사용했다. [YAMNet 모델 카드](https://www.kaggle.com/models/google/yamnet/tfLite/classification-tflite/1), [TUT 녹음과 이용 조건](https://zenodo.org/records/1040179)

시각 검증에는 Niu 등이 CVPR 2025에서 발표한 OVO-Bench의 문항과 채점 코드를 사용했다. 벤치마크에는 CC BY-NC-SA 4.0이 적용되며 원본 영상은 각 데이터셋의 이용 조건을 따른다. [프로젝트와 이용 조건](https://github.com/JoeLeelyf/OVO-Bench#license)

장면의 변화 시각을 비교할 때는 Shou 등의 Kinetics-GEBD 주석(CC BY-NC 4.0)을 썼다. 원본 영상은 Common Visual Data Foundation에서 배포한다. [주석과 이용 조건](https://github.com/StanLei52/GEBD), [Kinetics 영상](https://github.com/cvdfoundation/kinetics-dataset)

낭독 비교에는 Google FLEURS의 벵골어, 영어, 힌디어, 한국어, 중남미 스페인어, 태국어 시험 자료를 썼다. 녹음과 기준 문장의 이용 조건은 CC BY 4.0이다. [녹음과 기준 문장](https://huggingface.co/datasets/google/fleurs)

사람과 GPT가 같은 발화를 들은 실험에는 Magnotti 등(2024)이 CC0으로 공개한 영상과 연구진이 집계한 참가자 응답을 썼다. [실험 영상](https://openwetware.org/wiki/Beauchamp:Stimuli), [연구진의 분석 자료](https://zenodo.org/records/10689633)

발음 비교 실험에는 Meta가 Apache-2.0으로 공개한 XLSR 음소 인식기를 썼다. [모델 카드](https://huggingface.co/facebook/wav2vec2-xlsr-53-espeak-cv-ft), [발음 비교 결과](perception-check.md#같은-발화를-들은-사람-28명과-gpt)

- 시선: [UNISAL](https://github.com/rdroste/unisal)(Apache-2.0)
- 소리 분리: [Demucs](https://github.com/facebookresearch/demucs)(MIT), [audio-separator](https://github.com/nomadkaraoke/python-audio-separator)(MIT)의 Mel-Band Roformer 관중 모델(aufr33, viperx)
- 발화 구간 검출: [Silero VAD](https://github.com/snakers4/silero-vad)(MIT)
- 받아쓰기: [Whisper](https://github.com/openai/whisper)(MIT)를 [mlx-whisper](https://github.com/ml-explore/mlx-examples)와 [faster-whisper](https://github.com/SYSTRAN/faster-whisper)로 실행
- 화면 글자: [ocrmac](https://github.com/straussmaximilian/ocrmac)로 Apple Vision 사용
- 싱크: [Synchformer](https://github.com/v-iashin/Synchformer)(MIT)
- 소리 분류: [AST AudioSet 모델](https://huggingface.co/MIT/ast-finetuned-audioset-10-10-0.4593)(BSD-3)
- 소리 설명과의 유사도 비교에는 LAION의 [CLAP HTSAT 모델](https://huggingface.co/laion/clap-htsat-unfused)(Apache 2.0)을 쓴다.

소리의 정체를 비교할 때는 Meta가 Apache 2.0으로 공개한 PE-AV Large의 소리 및 텍스트 인코더를 쓴다. 생활음 녹음과 분류는 Karol Piczak의 ESC-50 자료(CC BY-NC 3.0)에서 가져왔다. [모델](https://huggingface.co/facebook/pe-av-large), [녹음과 이용 조건](https://github.com/karolpiczak/ESC-50)

참조 녹음은 Freesound에서 CC0으로 공개한 파일을 쓴다. 소리 이름은 Fonseca 등이 공개한 FSD50K(CC BY 4.0)에서 가져왔다. 저장소에는 녹음 번호와 체크섬을 담았다. [FSD50K 배포본과 논문, 이용 조건](https://zenodo.org/records/4060432)

점 추적에는 Google DeepMind가 Apache-2.0으로 공개한 온라인 BootsTAPIR 코드와 가중치를 쓴다. [코드와 가중치](https://github.com/google-deepmind/tapnet)

겹친 소리를 다시 들을 때는 Google Research가 YFCC100M으로 학습한 MixIT 모델을 쓴다. 연구진이 배포한 가중치의 라이선스는 Apache-2.0이다. [모델과 라이선스](https://github.com/google-research/sound-separation/tree/master/models/neurips2020_mixit#yfcc100m-model-checkpoint)

청취 비교 실험에는 Google의 Gemma 4 E4B-IT를 mlx-community가 4비트로 변환한 모델도 썼다. 원본 모델의 라이선스는 Apache-2.0이다. [Google 모델 카드](https://huggingface.co/google/gemma-4-E4B-it), [MLX 변환본](https://huggingface.co/mlx-community/gemma-4-e4b-it-4bit)

유사한 녹음의 설명은 탐페레대학교 Audio Research Group의 Konstantinos Drossos, Samuel Lipping, Tuomas Virtanen이 공개한 Clotho v2.1 개발 자료에서 가져온다. 설명문은 실험과 비상업적 용도로 사용할 수 있다. 설치하면 원본 CSV 및 검색 색인과 함께 저작권 고지 전문이 저장된다. [데이터와 이용 조건](https://zenodo.org/records/4783391)

NVIDIA Audio Flamingo Next에는 비상업적 이용을 위한 NVIDIA OneWay Noncommercial License가 적용된다. 연구용 설치 명령은 audio.cpp의 Q4_K 변환본과 macOS 실행 라이브러리를 따로 받는다. [NVIDIA 모델과 이용 조건](https://huggingface.co/nvidia/audio-flamingo-next-hf), [GGUF 변환본](https://huggingface.co/audio-cpp/Audio-Flamingo-GGUF), [audio.cpp 배포본](https://github.com/0xShug0/audio.cpp/releases/tag/v0.9.6)

선택형 원음 묘사는 Qwen 팀의 Qwen3-Omni와 mlx-community의 8비트 MLX 변환본을 쓴다. 모델 카드의 라이선스는 Apache-2.0이다. [원본 모델](https://huggingface.co/Qwen/Qwen3-Omni-30B-A3B-Instruct), [MLX 가중치](https://huggingface.co/mlx-community/Qwen3-Omni-30B-A3B-Instruct-8bit)
- 그 밖에 librosa, OpenCV, PyTorch

상업적으로 쓰기 전에 모델마다 라이선스를 확인할 것.

예시 그림의 음악은 Kevin MacLeod(incompetech.com)의 "Crypto"(Creative Commons Attribution 4.0)이다. 내레이션은 macOS `say`로 만들었다.

사람이 소리만 들었을 때와 화면까지 봤을 때 쓴 설명은 Sudarsanam, Martín Morató, Hakala, Virtanen의 AVCaps에서 가져왔다. 저자들이 CC BY-NC 4.0으로 공개한 자료를 비상업 연구에 사용했다. [AVCaps 자료와 이용 조건](https://huggingface.co/datasets/TUT-ARG/AVCaps), [실험 결과](listening-check.md#사람들이-낯선-영상을-한-문장으로-설명한-경우)

음높이 측정에는 Bittner, Pasalo, Bosch, Meseguer-Brocal, Rubinstein이 CC BY 4.0으로 공개한 독창 녹음 40개와 음악가의 주석을 썼다. [vocadito 녹음과 주석](https://zenodo.org/records/5578807)

영상 설명을 비교하는 `examples/check_first_impressions.py`에서는 Apache-2.0으로 공개된 all-MiniLM-L6-v2로 문장을 벡터로 바꾼다. [문장 모델과 사용법](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2)

발화 검증에는 [ESC-10](https://github.com/karolpiczak/ESC-50)(CC BY 3.0)과 [Free Spoken Digit Dataset](https://github.com/Jakobovski/free-spoken-digit-dataset)(CC BY-SA 4.0)의 녹음을 썼다.

두 소리를 겹쳐 듣는 실험에는 Karol J. Piczak이 공개한 ESC-50(CC BY-NC 3.0)의 50개 분류에서 녹음을 한 편씩 썼다. [자료 이용 조건과 녹음별 출처](https://github.com/karolpiczak/ESC-50/blob/master/LICENSE)

화면과 소리를 검증할 때는 Google DeepMind의 Perception Test에서 공개한 영상과 문항, 소리의 출처가 화면에 보이는지를 기록한 주석을 썼다. [Perception Test, CC BY 4.0](https://github.com/google-deepmind/perception_test)
입력에 따라 GPT가 무엇을 맞히고 어디서 답을 보류했는지는 [화면 검증 기록](perception-check.md)에 정리했다.

처음 듣는 소리를 묘사할 때는 Étienne Labbé, Thomas Pellegrini, Julien Pinquier의 CoNeTTE 모델(MIT)을 쓴다. [모델과 한계](https://huggingface.co/Labbeti/conette), [구현](https://github.com/Labbeti/conette-audio-captioning)

현장 녹음 실험에는 Lipping, Sudarsanam, Drossos, Virtanen이 공개한 Clotho-AQA의 질문과 답(MIT) 및 Clotho v2.1의 소리 묘사를 썼다. 녹음마다 Freesound 원작자가 정한 이용 조건이 있고 소리 묘사에는 탐페레 대학교의 이용 조건이 적용된다. [Clotho-AQA](https://zenodo.org/records/6473207), [Clotho v2.1](https://zenodo.org/records/4783391)

화자의 설명과 화면 위치를 비교할 때는 Pont-Tuset, Uijlings, Changpinyo, Soricut, Ferrari의 Localized Narratives에서 공개한 녹음과 시각별 문장, 마우스 좌표(CC BY 4.0)를 COCO 검증 사진과 함께 사용했다. [Localized Narratives](https://google.github.io/localized-narratives/), [COCO 이용 조건](https://cocodataset.org/#termsofuse)

원음 청취 실험에서 Gemma 4 E2B IT를 시험했다. 원 모델 카드는 Apache 2.0, 실험에 쓴 MLX 변환본은 Gemma 라이선스를 표기한다. [Google 모델 카드](https://huggingface.co/google/gemma-4-E2B-it), [MLX 변환본](https://huggingface.co/mlx-community/gemma-4-e2b-it-4bit)

표지판을 읽는 실험에는 COCO 사진과 COCO-Text V2의 전사를 썼다. 사진마다 원작자가 정한 이용 조건이 있다. [COCO-Text V2](https://bgshih.github.io/cocotext/), [COCO 사진 이용 조건](https://cocodataset.org/dataset/termsofuse.htm)

영상을 자유롭게 보는 사람들의 시선을 비교할 때는 Mital, Smith, Hill, Henderson의 DIEM 연구 자료를 썼다. 비상업적 연구에 CC BY-NC-SA 3.0 조건으로 사용할 수 있다. [DIEM 자료와 이용 조건](https://jhenderson.org/vclab/DIEM.html)

헤드폰 방향 검증에는 Wierstorf, Spors, Raake가 공개한 Two!Ears 실험 자료(CC BY 4.0)로 정면을 향한 머리의 양쪽 귀에 도달하는 소리를 재현해 참가자들이 가리킨 방향과 비교했다. 화면 속 소리의 출처를 찾는 실험에는 SSL-SaN 저자들이 공개한 가중치와 MIT 라이선스 코드를 썼다. [Two!Ears 자료와 이용 조건](https://docs.twoears.eu/en/latest/database/listening-tests/localisation/), [SSL-SaN](https://github.com/xavijuanola/SSL_SaN)

자동으로 소리의 위치를 찾는 실험에는 SLAVC 저자들이 VGG-Sound 144k로 학습한 모델과 Apache-2.0으로 공개한 구현을 썼다. [SLAVC 논문, 코드, 가중치](https://github.com/stoneMo/SLAVC)

청각 실험에는 탐페레 대학교 Machine Listening Group의 MACS 주석과 탐페레 공과대학교 Audio Research Group의 TAU Urban Acoustic Scenes 2019 녹음을 비상업 실험용 라이선스로 사용했다. [청각 검증 기록](listening-check.md), [MACS](https://zenodo.org/records/5114771), [TAU 녹음](https://zenodo.org/records/2589280)

</details>
