<div align="center">

# Reel

**언어 모델에게 영상을 보는 눈과 귀를**

Reel은 영상을 재생하면서 그때까지 나온 화면과 녹음을 모델에 건넨다. 그림과 글을 읽는 모델은 같은 녹음을 들은 로컬 청취기의 설명도 읽을 수 있다. 눈에 띈 순간을 되짚거나 30초씩 페이지를 넘기며 영상을 따라갈 수 있다.

![python](https://img.shields.io/badge/python-3.11%2B-3776AB)
![platform](https://img.shields.io/badge/macOS-Apple%20Silicon-black)
![license](https://img.shields.io/badge/license-MIT-green)

[English](README.md)

<img src="assets/reel_page_1.png" alt="예시 영상의 첫 30초를 한 시간축에 펼친 Reel 페이지" width="100%">

</div>

## 풀려는 문제

말하는 사람이 화면에 들어오기 전에 목소리가 먼저 들릴 수 있다. 두 사진에서 손의 위치가 같아도 그사이에 손은 움직였다가 돌아왔을 수 있다. 모델이 이런 순간을 따라가려면 화면과 녹음을 같은 시간축에서 보고 들을 수 있어야 한다. 소리가 어디서 났는지 아직 모를 때는 그만큼만 말할 수 있어야 한다.

편집한 영상은 이를 시험해 볼 한 가지 방법이다. 해설 1초가 두 번 재생되거나 소리가 갑자기 끊기거나 자막이 목소리보다 먼저 뜬다면, 처음 보는 모델도 그 변화를 알아차릴 수 있을까?

## 아이디어

글을 읽는 모델에게는 읽는 순서가 곧 시간이다. 그러니 영상 전체를 빠짐없이, 읽는 순서대로 주면 된다.

- **빈틈 없음.** 모든 프레임과 10ms 단위의 모든 소리가 페이지 어딘가에 흔적을 남긴다. 표본을 뽑지 않고 픽셀 열마다 최댓값을
  남기기 때문에 한 프레임짜리 번쩍임이나 1초짜리 반복도 사라지지 않는다.
- **하나의 시간축.** 화면, 자막, 소리, 음량, 관중, 말이 같은 시간축에 놓이므로 목소리가 그 화면의 것인지, 컷과 소리가 같이 바뀌는지가 보인다.
- **기억.** 시청자는 "방금 들었는데", "아까 본 장면인데" 하고 느낀다. Reel은 둘 다 표시한다. 같은 녹음이 다시 재생되면
  빨간색으로, 비슷한 장면이나 같은 사람의 목소리가 돌아오면 처음 나온 시점과 함께 적는다.
- **읽고 나서 판단.** 페이지는 30초씩 앞으로만 진행한다. 독자는 요약을 보기 전에 한 장씩 읽으며 짧은 기록을 남기고,
  페이지에는 판정을 적지 않는다.

## 페이지 구성

<img src="assets/reel_page_2.png" alt="예시 영상의 두 번째 페이지" width="100%">

| 줄 | 내용 | 시청자가 알아채는 것 |
|---|---|---|
| frames over time | 고른 프레임을 실제 표시 시각에 배치. 다음 프레임이 늦게 나오면 앞 화면이 계속 보임. 색 변환을 지원하는 FFmpeg에서는 PQ나 HLG로 표시된 HDR 영상을 sRGB 그림으로 변환 | 누가 어떤 물체와 함께 보이는지 확인한다. 재생 중 한 부분에 변화가 두드러지면 그 위치와 전후 시각이 나온다. `look`으로 두 순간의 원본을 확대할 수 있다. [글자 읽기 실험](docs/perception-check.md#잠깐-나타난-글자를-다시-읽을-때) |
| follow (재생 중) | 앞에서 짚은 점이 프레임마다 어디로 움직였는지 | 처음 짚은 곳과 마지막으로 찾은 곳을 확대해 같은 손이나 물건인지 확인 |
| subject | 영상의 주인공이 화면 어디에 있는지, 시선이 거기로 가는지 | 주인공을 놓치는 장면 |
| on screen | 글자와 숫자를 읽어 해당 프레임이 보이는 동안 표시한다 | 소리가 나는 순간에 어떤 점수나 표지판이 보였는지 확인한다 |
| change | 프레임 간 변화, 밝기, 정지 표시. MCP에서 `ask_scene(exposure_detail=true)`로 짧은 동작을 다시 보면 각 프레임 아래에 그동안 녹음된 최대 음량이 나온다. `sync_regions`로 영역을 고르면 사각형 전체의 움직임을 볼 수 있다 | 컷, 번쩍임, 암전, 멈춘 화면 |
| seen again | 앞에서 나온 장면 | 돌려 쓴 영상 |
| sound | 주파수 대역마다 소리의 세기를 −80~0 dBFS의 고정 눈금으로 표시 | 다른 영상이나 확대 화면에서도 같은 소리는 같은 밝기로 보인다. 재생 중에는 길게 이어지는 음이나 짧게 치는 소리를 골라 원음과 대조할 수 있다. 목소리가 끊기는 구간에는 채널별로 남은 소리의 크기도 나온다. 재생할 때는 반복이 뚜렷한 소리의 추정 주파수도 나온다 |
| sounds like | 페이지에는 소리가 갑자기 변하는 시점이 나온다. 재생 중에는 어느 채널의 어떤 주파수 대역이 얼마나 커지거나 작아졌는지도 표시한다. `stream.md`에는 최대 10초, `play`에는 채널별로 최대 4초 동안 들린 소리 후보가 나온다. 문장으로 묘사할 때는 최대 10초를 듣는다. MCP에서는 WAV 첨부 여부와 관계없이 첫 재생부터 로컬 청취기의 설명과 두 번의 받아쓰기에서 일치한 단어도 읽을 수 있다. 다시 들을 때는 초마다 나온 소리 후보와 원음을 이어서 비교한 결과를 함께 볼 수 있다 | 짧은 타격음, 그 뒤에 깔린 음악, 북적이는 방에서 들리는 말소리 |
| level | 10ms마다 잰 전체 음량. 장면별 텍스트에는 구간별 중앙값과 가장 큰 순간의 음량 및 시각을 기록 | 서서히 줄어드는 소리, 조용한 배경에서 짧게 튀는 소리 |
| L level, R level | 채널별로 표시한 10ms 단위 음량. 채널이 셋 이상이면 번호로 표시 | 소리가 한쪽에만 있는지, 왼쪽에서 오른쪽으로 옮겨 가는지 |
| 1 early, 2 early | 같은 파형이 어느 채널에 먼저 도달했는지 0.25초 단위로 표시 | 양쪽 음량이 같아도 한쪽 귀에서 먼저 들릴 수 있다. 빈 구간은 시간차를 판별하기 어려운 곳이다. |
| film time, pixel change, ch peak, ch bands Hz | 처음 볼 때는 한 칸의 가운데 시각에 보인 화면과 그 칸에서 달라진 모습을 보여준다. `observe`로 다시 보거나 `view(focus=...)`로 확대할 때는 각 줄을 왼쪽부터 읽는다. 사진 아래 선을 따라가면 그 순간에 어느 채널에서 어떤 높이의 소리가 났는지 볼 수 있다. 주파수 띠는 해당 대역의 소리가 클수록 밝다. 화면을 확대해도 소리는 전체 장면의 녹음을 쓴다. | 짧은 동작이 소리보다 앞섰는지 뒤따랐는지 확인 |
| joint (선택 기능) | 같은 원음을 따로 듣고, 움직이는 화면과 다시 보면서 쓴 설명. 재생 중에 앞선 장면까지 포함해 질문할 수 있다 | 소리가 나는 동안 눈앞에서 이어진 동작을 확인 |
| in sync? | 소리가 화면보다 빠른지 늦은지, 2초 빠름부터 2초 늦음까지 매 순간 | 말보다 먼저 움직이는 입, 소리보다 먼저 맞는 공 |
| crowd stem | 분리 모델이 추정한 관중 소리를 원음과 같은 대역별 눈금으로 표시 | 구호처럼 반복되는 리듬이 원음에서도 들릴 만큼 큰지 비교 |
| said | 재생 중 소리를 키워야 검출된 목소리는 추정 시각만 표시한다. 다시 볼 때는 `heard_words`로 지정한 구간의 말소리를 따로 확인할 수 있다. 두 구간에서 단어와 시각이 일치한 대사. 분리한 트랙에서만 발화가 감지되면 원음과 대조하고 증폭해 확인한 후보는 `reel listen`에 표시 | 무슨 말인지, 자막과 맞는지 |
| heard again | 같은 녹음이 다시 재생되면 빨간색(3초 안의 메아리, 먼 곳에서 붙여넣은 구간 모두), 비슷한 목소리가 돌아오면 그 시점. 다시 들을 때는 `compare_heard(similarity=true)`로 두 구간의 녹음이 얼마나 비슷한지도 비교할 수 있다 | 편집 실수로 생긴 반복, 돌려 쓴 관중 녹음 |

## 시험 결과

로컬 Codex GPT로 실사 16편을 재생한 결과, 현재 Reel에서 사람 주석과 일치한 답은 **48개 중 36개(75.0%)**였다. 화면 내용은 14/16, 지정 구간에서 난 소리는 11/16, 그 소리를 낸 대상이 화면에 보이는지는 11/16이었다. 오답은 8개였고 4개는 판단을 유보했다. [영상별 응답과 측정 조건](docs/perception-check.md#같은-48문항으로-다시-잰-일치율)

다른 실사 12편에서는 나중에 난 소리가 앞의 두 소리 중 어느 쪽과 같은 종류인지 물었다. GPT가 도구를 자유롭게 골랐을 때 사람의 소리 분류와 일치한 답은 기존 버전 4/12, 원음끼리 비교할 수 있는 버전 8/12였다. [영상별 응답과 두 후보에 정답이 없는 경우](docs/listening-check.md#이름을-모르는-소리를-전에-들은-녹음과-비교)

동시에 보이는 두 장면 중 어느 쪽 녹음이 재생되는지 GPT는 14번 중 9번 맞혔다. 소리를 바꿨을 때 두 번 다 맞힌 쌍은 7쌍 중 3쌍이었다. 별도 실사 8편에서는 소리를 낸 대상이 보이는지 매번 판단을 유보했다. [영상별 응답과 이전 버전 비교](docs/perception-check.md#화면-가장자리에서-난-소리)

프레임 단위 상세 재생을 추가한 직후 같은 실사 16편을 다시 봤을 때 사람 주석과 일치한 답은 **48개 중 35개(72.9%)**였다. 화면 내용은 15/16, 소리 종류는 10/16, 소리를 낸 대상이 보이는지는 10/16이었다. 별도로 같은 영상의 12개 프레임에 짧은 소리를 한 번씩 옮겼을 때 그 순간 화면에 뜬 숫자는 기존 재생에서 6번, 상세 재생을 쓸 수 있을 때 12번 모두 맞혔다. [재생 방식별 결과](docs/perception-check.md#소리가-난-프레임을-다시-보는-시험)

앞선 버전은 당시 처음 쓰는 영상 16편에서 **48문항 중 38개(79.2%)**를 맞혔다. 소리 여덟 종류마다 출처가 보이는 영상과 가려진 영상을 한 편씩 골랐고 보기만 읽고 추측한 GPT는 같은 48문항 중 23개를 맞혔다. 기존 28편을 포함한 44편의 일치율은 **76.5%(101/132)**였다. [새 영상 16편의 결과](docs/perception-check.md#처음-사용한-영상-16편)

로컬 Codex의 GPT가 같은 버전의 Reel로 실사 영상 28편을 다시 봤다. 사람 주석과 일치한 답은 **84문항 중 63개(75.0%)**였다. 화면 내용 25/28, 지정한 구간에서 난 소리 20/28, 소리를 낸 대상이 보였는지 18/28이었고 12개를 틀리고 9개는 유보했다. 영상마다 새 세션을 열어 세 시험의 기존 문항으로 측정했으며 GPT에는 화면과 로컬 소리 분석 결과를 전달했다. [영상별 답과 채점 방법](docs/perception-check.md#같은-버전으로-다시-본-실사-영상-28편)

도시 녹음 23개에서 GPT와 청취자가 고른 소리는 평균 **56.3%** 겹쳤고 사람끼리는 **58.9%** 겹쳤다. 청취자 과반이 아이 목소리를 고른 세 곳에서 GPT는 한 번도 아이 목소리를 고르지 않았다. [녹음별 응답과 계산 방법](docs/listening-check.md#도시-녹음에서-gpt와-청취자가-고른-소리)

정답을 모르는 새 독자(읽는 법 [안내서](docs/READING.md)와 페이지만 받은 모델)에게 사람이 이미 평가한 팬 영상을 보여줬다.

| 영상 | 사람이 한 지적 | 독자가 지시 없이 알아챈 것 |
|---|---|---|
| 10분짜리 e스포츠 다큐 1차본 | 연도 순서가 뒤죽박죽, 갑작스러운 암전, 해설 1초가 두 번 재생, 자막과 목소리 어긋남, 멈춘 화면 | 1차 시험 5개 중 4개, 반복 듣기를 넣은 뒤 **5개 전부** |
| 6분짜리 선수 헌정 영상 1차본 | 카드마다 같은 팀 구호 반복, 뚝뚝 끊기는 연결, 급한 후반, 스코어 바가 잘린 레터박스 게임 화면, 주인공 포커스 부족 | **5개 중 4개**: 구호(소리만으로 구호 구간 4곳 모두 **T1**으로 맞춤), 완전 무음 구간, 쌓인 정지 카드, 높이 74~79%로 잘려 스코어 바가 사라진 게임 화면. 포커스는 "후반 우승 콜이 다른 선수 이름으로 나온다" 정도로만 짚음 |
| 2분짜리 하이프 트레일러, **Reel을 만들 때 한 번도 안 쓴 영상** | 번개 효과음이 손바닥 찰싹 소리 같음, 의미 없는 장면, 싸 보이는 음악 | **3개 전부**: "타격음 9개 중 2개가 찰싹 소리로 들린다", 물 튀는 소리, 자동차, 오리 소리로 들리는 타격음. 같은 정지 화면 두 장 위로 13초 동안 연도만 바뀌는 구간과 검은 화면으로 시작하는 오프닝. 배경음악은 "무료 스톡 음악"으로 봄(이 판단은 소리만큼 크레딧 글자에도 기댔음) |
| 1분 30초짜리 하이프 트레일러, **Reel을 만들 때 한 번도 안 쓴 영상** | 임팩트 없는 장면, 화면과 안 맞는 글리치 효과, 엉뚱한 선수의 처치 배너, 낮은 화질 | **4개 전부**: "영상이 보여줄 장면이 떨어진 느낌", 기자회견 한 장면을 5초 동안 깜빡이는 글리치, 제우스 데뷔 구간의 페이커 처치 배너, 흐리고 늘린 것 같은 화면 |
| 같은 다큐의 수정본 | 아직 없음 | 편집자 검수에서 빠진 문제를 찾음: 인용문 위에 깔린 해설, 장 전환마다 떨어지는 소리, 안 읽히는 기록표 |

### 웹에서 무작위로 가져온 영상

장르별 검색에서 무작위로 고른 영상 26개로 시험했다. 토크쇼, 지역 뉴스, 애니메이션, 스케이트와 드럼 영상, 요리, 드론 영상, 축구와 테니스와 F1 하이라이트, TED 강연, 예고편, 팟캐스트, 라이브 음악, 충돌 시험 모음, 체스 방송, 브이로그까지 섞었다.

스크립트가 반복, 메아리, 암전, 정지, 번쩍임, 레터박스, 저화질 구간, 소리 끊김, 볼륨 점프, 화면보다 빠르거나 늦은 소리를 무작위 위치에 심었지만 정답지는 독자가 볼 수 없게 따로 뒀다. 라운드마다 영상 하나는 손대지 않고 남겨서 독자가 없는 문제를 지어내는지도 봤다.

| 라운드 | 심은 실수를 알아챈 수 | 손대지 않은 영상 |
|---|---|---|
| 1 | 15개 중 9개 | 원본 편집자의 페이드를 갑작스러운 암전이라고 함 |
| 2 | 15개 중 14개 | "문제 없음" |
| 3 | 15개 중 10개 | "문제 없음" |
| 4 | 14개 중 10개 | "문제 없음" |
| **5: 코드를 고정하고 이후 수정 없음** | 17개 중 12개(싱크 어긋남이 남긴 말 더듬기로 알아챈 경우까지 치면 14개) | "문제 없음" |

1라운드부터 4라운드 사이에는 독자가 놓친 것을 보고 뚝 끊기거나 갑자기 커지는 소리, 몇 프레임짜리 번쩍임, 주변보다 갑자기 흐려진 화면, 화면과 소리의 싱크, 같은 녹음의 재사용을 감각으로 추가했다. 따라서 이 사이의 점수에는 고친 결과가 섞여 있다. 정직한 숫자는 새 영상으로 시험하면서 코드를 건드리지 않은 5라운드다.

5라운드를 종류별로 보면 레터박스 3개 중 3개, 암전 3개 중 3개, 메아리 3개 중 3개, 붙여넣은 반복 1개 중 1개, 소리 끊김 2개 중 1개, 빠르거나 늦은 소리 3개 중 1개, 정지 1개 중 0개, 볼륨 점프 1개 중 0개다. 잘못 짚은 것은 하나로, 체스 방송의 허밍을 갑작스러운 볼륨 점프로 표시했다. 다섯 라운드 내내 손대지 않은 영상에서 없는 문제를 만들어 낸 적은 없다.

## 빠른 시작

Apple Silicon 맥용이다(글자 인식에 Apple Vision, 받아쓰기에 MLX를 쓴다). `ffmpeg`가 필요하다.

```bash
git clone <이 저장소> reel && cd reel
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[all]"           # 필요한 것만: separate, crowd, mlx, speech, ocr, gaze
reel setup --sync                  # 시선 모델과 싱크 모델(1.1GB) 받기
reel doctor                        # 이 맥에서 무엇을 보고 들을 수 있는지

reel play film.mp4 --out reels/first-view --until 8.5
reel play film.mp4 --out reels/sound-only --until 8.5 --tracks sound
reel watch film.mp4 --out reels/film --subject Zeus --genre lol --lang ko
reel watch film.mp4 --until 8.5 --out reels/view-01
reel zoom reels/film --t0 72 --t1 80          # 한 구간을 자세히 다시 보기
reel frame reels/film --at 73.25              # 73.25초에 보이던 원본 화면 꺼내기
reel frame reels/film --t0 73 --t1 74         # 이 1초 동안 표시된 모든 프레임 보기
reel listen reels/view-01 --t0 4 --t1 7       # 4초부터 7초까지 채널별 음량과 변화 시각 확인
reel observe reels/view-01 --t0 4 --t1 4.5    # 각 화면이 떠 있던 동안 재생된 소리 확인
```

모델이 무엇을 보고 무엇을 들었는지 비교하려면 같은 파일을 새 세션에서 세 번 연다. `--tracks picture`는 화면만, `--tracks sound`는 소리만 재생한다. 기본값 `--tracks both`는 둘을 함께 재생한다. `play`와 `mcp` 모두 원본의 시각과 `--until`을 지킨다. 지나간 장면을 다시 확인할 때도 처음에 고른 트랙만 쓴다. [영상 여덟 편에서 사람과 GPT가 알아차린 것](docs/perception-check.md#같은-영상을-보거나-듣거나-둘-다-했을-때)

영상이나 소리 트랙이 여러 개면 각 종류에서 기본 표시가 붙은 첫 트랙을 고른다. 기본 표시가 없으면 재생 가능한 첫 트랙을 쓰고 앨범 표지와 미리보기용 썸네일은 영상에서 제외한다. 다시 보기와 받아쓰기에서도 같은 트랙을 쓴다.

음높이를 가늠할 수 있는 구간에서는 각 채널의 추정 주파수를 `audio.pitch_contour`로 보낸다. 무엇이 낸 소리인지 몰라도 음이 올라갔는지 내려갔는지 살펴볼 수 있다.

MCP를 쓰는 모델은 Reel에서 화면과 녹음을 바로 받을 수 있다. `pip install -e '.[mcp]'`로 설치하고 로컬 stdio 서버의 실행 명령을 `reel mcp /path/to/film.mp4 --until 8.5`로 지정한다. Codex CLI에서는 Reel 환경을 활성화한 상태에서 다음과 같이 등록할 수 있다.

```bash
codex mcp add reel -- "$(command -v reel)" mcp /path/to/film.mp4 --until 8.5 --image-client
```

모델에 동영상을 건네려면 클라이언트에서 `view_media(turn=1, continuous_seconds=5)`를 호출한다. 응답의 MCP 링크를 순서대로 읽으면 당시 소리가 담긴 첫 5초를 사람도 재생해 볼 수 있다. 다음 턴에 `view`로 바꿔도 같은 시각부터 이어진다. 로컬 Codex CLI 시험에서 GPT에는 MOV의 인코딩된 바이트가 텍스트로 들어와 이 환경에서는 `view`를 쓴다. [재생 방법과 파일 형식](docs/READING.md#watching-a-little-at-a-time)

기본 `view(turn=1)`은 그 구간의 원음 WAV와 함께, 설치된 청취기가 녹음에서 들은 내용과 두 번의 받아쓰기에서 일치한 단어를 보낸다. 그림과 텍스트를 받는 모델도 같은 청취 기록을 읽을 수 있다. `--image-client`로 시작하거나 `native_audio=false`를 쓰면 WAV는 요청할 때만 붙는다. 원음과 측정값만 받으려면 기본 서버에서 `sound_estimates=false`로 호출한다.

클라이언트가 WAV를 생략해도 원음에서 잰 값과 로컬 청취 기록은 남는다. 이 경우 GPT는 무엇을 근거로 소리를 설명했는지 밝힐 수 있다. [목소리와 주변 소리 실험](docs/perception-check.md#목소리와-그-목소리가-가리키는-소리)

그림과 텍스트로 볼 때는 구간별 청취 결과가 먼저 나온다. 각 채널의 원음에서 잰 음량, 주파수별 에너지의 가운데 80%가 걸친 범위, 에너지가 가장 많이 모인 좁은 대역의 비율도 볼 수 있다. 음량이 같아도 한 음이 이어지는지, 잡음이 넓은 대역에 퍼졌는지 구별할 단서다.

스테레오에서는 어느 채널이 더 컸는지와 같은 파형이 어느 채널에 먼저 도착했는지를 첫 재생에서 볼 수 있다. 양쪽 음량이 같아도 도착 시각은 다를 수 있다. 같은 `view`에 `full_detail=true`를 붙이면 개별 측정값과 인코더에 들어간 프레임 시각이 나온다. [사람과 GPT의 첫 시청 결과](docs/perception-check.md#원래-녹음의-주파수와-첫-설명)

두 청취기는 `pip install -e ".[speech]"`와 `reel setup --speech --describe-sounds`로 준비한다. [같은 화면에 다른 녹음을 붙인 결과](docs/perception-check.md#wav를-첨부했을-때-gpt가-실제로-받아들인-소리)

모델에 영상을 봐 달라고 하면 `view`를 호출해 한 구간씩 받고 WAV 첨부를 선택한 클라이언트에는 그 구간의 원음도 전달된다. 소리가 뚜렷하게 달라진 순간에는 그 전후의 화면도 받는다. 서버에 `--describe-sounds`를 붙이면 재생이 멈출 때마다 로컬 청취기가 원음을 듣고 설명한다. `--gaze`를 붙이면 시선 모델이 고른 화면 일부도 시간순으로 볼 수 있다. 지나간 장면은 `observe(start_s, end_s)`로 원하는 시각을 골라 그때 뜬 프레임과 같은 구간의 녹음을 함께 확인한다. 그림과 텍스트를 받는 모델은 화면과 주파수 그림을 읽고 로컬 청취기에 소리를 묻거나 발화 기록을 확인할 수 있다. [도구 사용법](docs/READING.md#watching-a-little-at-a-time), [Codex MCP 설정](https://developers.openai.com/codex/mcp/)

기본 재생에서도 대표 화면이 놓친 변화가 있고 그 구간의 모습을 열두 칸 안에 다 담을 수 있으면 시간순으로 보여준다. 축소했을 때 같은 모습이 이어지면 한 칸에 담는다. 계속 움직이는 장면에서 빠른 동작을 따라보려면 `view(turn=1, max_frames=12)`를 호출한다. 그 구간에 뜬 모든 화면이 시간순으로 나오고 녹음도 같은 시각에 멈춘다. 한 번에 볼 프레임 수는 1~12장 사이에서 고를 수 있다. 터미널에서는 `reel play film.mp4 --out reels/frames --continuous --max-frames 12`를 쓴다. [빠른 동작과 순서가 뒤바뀐 장면을 본 결과](docs/perception-check.md#빠른-동작을-프레임-순서대로-본-경우)

계속 움직이는 화면에서도 손이나 물체가 대표 화면 사이에 잠깐 다른 모습을 보이면 원본 프레임을 최대 12장까지 시간순으로 더 보여준다. `selected_picture_pts_us`에 그 시각이 담긴다. [사람이 행동 시각을 표시한 촬영 영상으로 확인한 결과](docs/perception-check.md#움직이는-장면에서-대표-화면-사이에-보인-모습)

각 화면이 보이는 동안 녹음된 소리의 크기도 확인할 수 있다. `audio.during_pictures`에는 그 프레임에 해당하는 원음 구간과 채널별 피크, RMS 레벨이 들어 있다. 두 프레임 사이에 빠진 구간의 소리는 원음에 남는다.

대화나 동작을 장면 전환 너머까지 보려면 `view(turn=1, continuous_seconds=8)`을 쓴다. 지나간 화면과 같은 구간의 녹음을 함께 받고, 다음 호출에서 다시 짧게 볼 수 있다. 터미널에서는 `reel play film.mp4 --out reels/view --step 8 --continuous`로 8초씩 이어 본다. [사람과 GPT가 시청 후 기억한 내용](docs/perception-check.md#한-장면을-이어-본-뒤-기억한-내용)

동작이 지난번 멈추기 전부터 이어졌다면 `view(turn=N, joint_seconds=6)`으로 직전 6초를 살핀다. 로컬 청취기가 그동안 들린 원음을 먼저 듣고 같은 구간의 화면과 소리를 함께 본다. 그림과 텍스트를 받는 모델은 `joint`에서 두 설명을 읽을 수 있다. N번째 구간이 끝난 시각까지만 되짚으며 터미널에서 재생 중일 때는 `joint 6`을 입력한다. [사람이 표시한 동작과 소리로 비교한 결과](docs/perception-check.md#여러-번-멈춘-장면의-화면과-소리를-이어-볼-때)

이미 본 2.4~3.7초에 무엇이 움직였는지 궁금하면 `ask_scene(start_s=2.4, end_s=3.7, question="소리가 바뀔 때 무엇이 움직였어?")`로 그 구간만 다시 볼 수 있다. 질문을 주면 원음을 들은 로컬 모델이 답하고 시작과 끝 시각만 주면 화면과 채널별 음량, 주파수 대역이 바뀐 시각을 받는다. 원음도 받으려면 `native_audio=true`를 붙인다.

물이 흐르는 소리와 말소리가 각각 언제 들리는지 살필 때는 `ask_scene(start_s=2, end_s=7, alternatives=["water running", "a person speaking"])`로 같은 구간을 다시 본다. 채널별로 초마다 나온 소리 후보와 구간 전체를 이어서 들었을 때의 후보를 함께 보여 준다. 초 단위 비교는 모델 입력을 채우려고 짧은 소리를 반복한다. 연속 비교는 원음을 한 번만 쓰고 남은 입력을 0으로 채운다.

터미널에서는 재생을 멈추고 `match 2 7 --per-second`로 같은 구간을 확인할 수 있다. 소리 이름을 떠올리기 어려우면 `alternatives=[]`로 비슷한 소리 후보를 볼 수 있다. [촬영 영상과 환경음으로 확인한 결과](docs/perception-check.md#원음에서-소리-두-가지의-순서-짚기)

두 곳에서 보이는 움직임이 소리와 맞는지 궁금하면 `ask_scene(start_s=2, end_s=5, sync_regions=[[0.1,0.3,0.4,0.8],[0.6,0.3,0.9,0.8]])`로 비교한다. 번호가 붙은 사각형은 움직임을 재는 범위다. 가장자리까지 포함해 픽셀이 변한 시각을 아래 그래프에서 채널별 음량과 나란히 볼 수 있다. `reel setup --sync`로 동기화 모델을 설치하면 영역마다 움직임과 녹음의 타이밍이 얼마나 맞는지도 볼 수 있다.

화면의 한 부분을 고정해서 확대하려면 `view(turn=2, focus=[0.7, 0.4], max_seconds=0.3)`처럼 다음에 볼 위치와 시간을 고른다. 0.3초 안에 다시 멈추고, 그동안 해당 위치에서 일어난 움직임을 전체 화면과 소리와 함께 받는다. 마지막 화면에서는 전체에서 고른 위치와 해당 부분을 확대한 그림을 나란히 본다. 다음 구간에서 위치나 시간을 바꿀 수 있다. 이미 본 순간의 원본 픽셀은 `look(turn=2, at_s=0.15, box=[0.5, 0.2, 0.9, 0.6])`로 확인한다.

화면과 소리가 함께 바뀐 이유가 궁금하면 `view(turn=2, question="소리가 날 때 화면에서 무엇이 움직여?")`처럼 묻는다. 로컬 모델은 원음을 먼저 따로 듣고, 같은 구간을 소리와 화면으로 다시 보면서 질문에 답한다. 직전에 본 장면까지 필요하면 `joint_seconds=6`을 붙인다. `reel mcp FILM --joint --image-client`로 시작하면 연속 재생을 처음 볼 때부터 시청각 설명을 받는다. [영상 여덟 편을 본 사람과 GPT의 설명](docs/perception-check.md#사람들이-같은-영상을-설명한-문장)

화면 안에서 움직이는 물체도 따라볼 수 있다. 1.25초에 본 손잡이의 위치가 `[0.7,0.4]`였다면 `view(turn=2, focus=[0.7,0.4], follow_from_s=1.25)`를 호출한다. 원본 영상에서 처음 짚은 곳과 마지막으로 찾은 곳을 확대해 각각 시각을 표시한다. 원본을 다시 열 수 없으면 저장된 사진을 쓴다. 한 번에 최대 8초까지 따라가며 놓친 프레임에는 위치를 비워 둔다. 기본 설치에서는 인접한 프레임의 무늬를 따라간다. `reel setup --follow`로 BootsTAPIR를 설치하면 가려졌다 나타난 점도 다시 찾을 수 있다. [사람이 표시한 위치와 비교한 결과](docs/perception-check.md#움직이는-점을-따라본-결과)

모델이 소리 이름이나 대사에 대한 힌트 없이 먼저 보게 하려면 `view(turn=1, sound_estimates=false)`를 쓴다. 7초까지 본 뒤 `listen(turn=N, start_s=2, end_s=7, native_audio=true, describe=false)`를 호출하면 중간에 몇 번 멈췄든 2~7초의 녹음과 채널별 주파수 그림을 받는다. 들린 소리를 식별하기 어려우면 같은 구간에서 `describe=true`로 로컬 청취기의 의견을 들을 수 있다.

소리를 듣고 두 가지가 떠오르면 원음에 대조할 수 있다. 예를 들어 `listen(turn=3, start_s=2, end_s=5, describe=false, alternatives=["a voice humming", "an electric motor running"])`은 각 채널에서 두 설명이 녹음과 얼마나 비슷한지 돌려준다. `alternatives=[]`이면 비슷하게 들리는 소리 후보를 찾는다. 모델은 `reel setup --compare-sounds`로 준비한다. 점수가 높아도 실제 출처는 다를 수 있다.

여러 소리가 겹쳤을 때 `listen(turn=N, start_s=2, end_s=7, component="percussive")`를 쓰면 여러 주파수에 걸쳐 짧게 난 소리가 두드러진다. 일정한 주파수에서 이어진 소리를 들으려면 `"harmonic"`을 고른다. 주파수 그림과 요청한 WAV에는 고른 성분이 담기고 로컬 청취기는 그 성분을 원음과 함께 듣는다. 한 소리가 양쪽 성분에 모두 남을 수 있다. [겹친 소리 청취 결과](docs/listening-check.md#겹쳐-들리는-소리를-원음과-대조)

처음 보는 영상은 `play`로 바로 열 수 있다. 출력된 `view.json`을 열면 그동안 지나간 프레임, 소리의 주파수 그림, 마지막 화면과 원래 채널의 WAV를 볼 수 있다. `next`를 입력하면 이어서 보고, `next 0.4`를 입력하면 최대 0.4초 뒤에 다시 멈춘다. 끝낼 때는 `quit`을 쓴다. 모델을 연결할 때도 표준 입력으로 같은 명령을 보내면 된다. 화면이나 음량이 급변하면 조금 일찍 멈추고 조용한 구간도 순서대로 보여준다. `play`나 `mcp`에 `--pause-on-sound-change`를 붙이면 전체 음량이 비슷해도 주파수 대역별 세기가 급변할 때 멈춘다. 평소 재생에서도 같은 변화의 시각을 확인할 수 있다. 이미 받은 구간을 로컬에서 다시 들으려면 `reel listen reels/first-view/turn_00001`을 쓴다.

멈출 때마다 `audio.signal`을 보면 원음에 0이 아닌 샘플이 있었는지 알 수 있다. `reel setup --sound-labels`로 가중치를 받아 두면 `audio.source_estimates`에 직전 최대 4초의 소리 후보가 채널별로 나온다. 소리는 있어도 후보가 비어 있을 수 있다.

`reel setup --captions`를 한 번 실행하면 다음 재생부터 `audio.scene`에 방금 들린 소리를 묘사한 짧은 문장이 나온다. 처음 2초가 모이면 시작하고 직전 최대 10초까지 함께 듣는다. uv나 Python 3.10이 설치돼 있으면 setup이 소리 묘사에 쓸 환경을 따로 만든다. 잡음을 빗소리로, 일정한 음을 기계음으로 추측할 수 있으니 채널별 실측값과 함께 읽는다. [현장 녹음을 들은 사람들과 비교한 결과](docs/listening-check.md#선택지-없이-현장-녹음을-들었을-때)

여러 소리가 겹칠 때는 `reel setup --layers`로 MixIT를 준비한다. 재생을 멈추고 `layer 2 5 2`를 입력하면 2~5초에 녹음된 원음과 그 구간에서 두 번째로 큰 추정 성분을 받는다.

MCP에서는 5초까지 재생한 뒤 `listen_layer(turn=3, start_s=2, end_s=5, layer=2)`로 들을 수 있다. 기본 채널은 1이며 오른쪽 채널을 따로 들으려면 스테레오 녹음에서 `channel=2`를 지정한다. 원음은 녹음 당시의 샘플레이트로 받고 분리본은 16 kHz로 듣는다. 한 소리가 여러 성분에 나뉘거나 다른 소리와 섞여 나올 수 있다.

찾고 싶은 소리를 말로 지정할 수도 있다. `reel setup --compare-sounds`로 CLAP를 준비한 뒤 MCP에서 번호 대신 `focus="a distant voice"`를 주면 원음과 여덟 성분 중 그 설명에 가장 가까운 녹음을 고른다. 원음이 선택될 수도 있다. 파형을 듣고 묘사하는 모델에는 검색어를 주지 않는다. 오디오를 받는 클라이언트는 WAV를, 이미지를 받는 클라이언트는 스펙트럼과 녹음별 설명을 받을 수 있다. [생활음을 겹쳐서 들은 결과](docs/listening-check.md#겹친-생활음을-하나씩-다시-들었을-때)

앞서 들은 배경음에 어떤 소리가 더해졌는지 궁금하면 재생을 멈추고 `compare-heard 0 2 4 6`을 입력한다. 로컬 모델이 0~2초와 4~6초의 원음을 따로 듣고 구간마다 답한다. MCP에서는 3번째 턴이 6초에 도달했다면 `compare_heard(turn=3, reference=[0,2], target=[4,6])`으로 같은 구간을 들을 수 있다. 이미 재생된 구간을 각각 최대 8초까지 지정한다. 오디오를 받는 클라이언트는 두 WAV를 받을 수 있고 이미지를 받는 클라이언트는 두 스펙트럼과 각 구간의 설명을 받는다. 이름을 모르지만 전에 들은 소리와 비슷하다면 `similarity=true`로 두 녹음을 직접 비교할 수 있다. CLI에서는 `--similarity`를 쓴다. 비슷한 배경음도 점수에 영향을 준다.

4초까지 재생한 뒤 `match 2 4`를 입력하면 로컬 CLAP이 2~4초의 녹음과 비슷한 소리를 찾는다. `match 2 4 "fabric rubbing" "water running"`처럼 궁금한 소리를 덧붙이면 두 설명의 점수를 다른 소리 후보와 함께 볼 수 있다. 가중치를 `reel setup --compare-sounds`로 받아 두면 별도 창에서도 `reel match TURN --t0 2 --t1 4`로 같은 결과를 얻을 수 있다. [녹음만 바꿔 본 결과](docs/listening-check.md#화면을-두고-녹음만-바꿨을-때)

멈춘 자리에서 `listen`을 입력하면 로컬 오디오 모델이 직전 소리를 최대 30초까지 다시 듣고 설명한다. `listen Does the rhythm speed up?`처럼 궁금한 점을 물으면 답과 함께 녹음 당시 채널을 담은 WAV를 받는다.

별도 창에서는 `reel listen reels/first-view/turn_00005 --question "Does the rhythm speed up?"`로 물을 수 있다. [현장 녹음을 들은 사람들의 답과 비교한 결과](docs/listening-check.md#현장-녹음에서-궁금한-소리-다시-듣기)

재생을 멈추고 `speech`를 보내면 지금까지 들린 목소리를 확인할 수 있다. 발화로 추정한 구간과 두 번 받아썼을 때 일치한 단어를 따로 돌려주므로 목소리가 잡혀도 단어는 비어 있을 수 있다.

별도 창에서는 `reel speech reels/first-view/turn_00001`을 쓴다. 멈출 때마다 자동으로 받아쓰려면 `play`에 `--speech`를 붙인다. 언어를 알면 `--lang ko`처럼 지정할 수 있다.

동작이 보인 순간에 목소리도 들렸는지 확인하려면 `heard_words(turn=3, start_s=2.1, end_s=4.8)`을 호출한다. 재생 중에는 `speech 2.1 4.8`, 별도 창에서는 `reel speech TURN --t0 2.1 --t1 4.8`을 쓴다. 중간에 몇 번 멈췄든 그 구간의 원음만 들어 말소리로 추정한 시간과 알아들은 단어를 따로 돌려준다. `ask_scene`에서 질문을 하거나 소리 보기를 주면 같은 구간의 발화 기록도 함께 나온다.

어떤 언어인지 알면 멈춘 자리에서 그 언어로 다시 들을 수 있다. 예를 들어 벵골어는 재생 중에 `speech bn`을 입력하거나 MCP에서 `heard_words(turn=1, lang="bn")`을 호출한다. 지정한 언어는 그 요청에만 쓰인다. [여섯 언어의 낭독으로 확인한 결과](docs/perception-check.md#여섯-언어에서-실제로-인용할-수-있었던-말)

원음 청취기는 화면과 단어 후보를 가린 채 같은 채널의 소리를 듣는다. 같은 발화 구간에서 두 모델이 기존 단어를 하나 이상 함께 알아들으면 주변에서 놓친 말도 대조한다. 철자와 어순이 맞는 후보는 `possible_words`에서 볼 수 있다. 대사를 인용할 때는 `words`를 쓰고 후보를 언급할 때는 불확실하다고 밝힌다. 저장한 구간을 다시 들으려면 `reel speech TURN --verify`를 실행한다.

재생 중에 모델이 `look 0.2 0.1 0.6 0.5`를 보내면 그 자리에서 화면 일부를 원본 크기로 볼 수 있다. 네 숫자는 왼쪽 위를 기준으로 0부터 1까지 표시한 값이며 순서대로 왼쪽, 위, 오른쪽, 아래 경계를 뜻한다.

별도 창에서도 `reel look reels/first-view/turn_00001 --box 0.2 0.1 0.6 0.5`로 같은 PNG와 영상 시각을 얻을 수 있다. 사람들이 볼 만한 지점을 시선 모델에 맡기려면 `play`에 `--gaze`를 붙인다. [사람 시선과 비교한 결과](docs/perception-check.md#같은-화면에서-처음-눈에-띈-곳)

지나간 장면도 재생 중에 `look --at 1.25 0.2 0.1 0.6 0.5`로 다시 볼 수 있다. 1.25초에 떠 있던 화면에서 지정한 부분을 원본 해상도로 잘라 준다.

`--gaze`를 켜면 직전 1초 동안 시선 모델이 어느 부분을 짚었는지 `gaze.recent_details.file`에서 볼 수 있다. [사람이 실제로 본 곳과 비교한 결과](docs/perception-check.md#소리가-달라지기-전후의-시선)

재생 중 멈춘 상태에서 `observe 2 4 --focus 0.1 0.3 0.5 0.8`을 입력하면 2~4초 사이에 지정한 영역이 어떻게 움직였는지 다시 본다. `action_view.pages`의 그림에서는 주황색 선이 해당 영역의 변화량을, 파란색 선이 원래 채널의 음량 피크를 보여준다.

뒤에 `--question Does the sound repeat?`를 붙이면 로컬 오디오 모델이 같은 구간을 듣고 답한다. 짧은 동작은 `action_view.frame_pages`에서 지정한 부분의 모든 프레임을 펼쳐 볼 수 있다. 다음 재생은 멈췄던 시각에서 이어진다.

멈춘 자리에서 `observe 2 4 --joint`를 입력하면 로컬 모델이 2~4초의 원음을 먼저 귀로만 듣고 같은 소리를 화면과 함께 다시 살핀다. 두 설명은 `joint.windows`에서 읽을 수 있다. 특정 동작이 궁금하면 명령 뒤에 `--question 소리가 시작될 때 무엇이 움직였어?`를 붙인다.

화면이 먼저 끝나거나 프레임이 많아 장면을 나눠 봐도 `joint.recording`에서 그 구간의 소리를 이어서 들은 설명을 읽을 수 있다. 질문을 하면 원음만 들은 답도 함께 온다. 화면이 없는 녹음에도 같은 명령을 쓸 수 있다.

매번 멈출 때 이 결과를 받으려면 `reel play film.mp4 --out reels/view --until 8.5 --joint`를 쓴다. 설치 방법은 아래 Qwen3-Omni 항목에 있다. [같은 화면에서 녹음만 바꿨을 때 GPT의 답](docs/perception-check.md#같은-화면에-다른-영상의-녹음을-붙인-실험)

스테레오 녹음에서는 `play`, `observe`, `listen`이 두 채널의 음량과 도착 시간 차이를 `stereo_cues`에 담는다. 서라운드 녹음에서도 좌우 스피커를 짝지을 수 있으면 같은 값을 읽을 수 있다. 화면 밖 소리도 한쪽 귀에서 들릴 수 있으므로 소리를 낸 물체를 찾을 때는 화면의 동작을 함께 본다.

`play`와 `observe`는 녹음에 뚜렷한 변화가 있으면 그 직전과 직후의 화면을 `sound_views`에 모아 준다. 마지막 칸을 보면 화면 어디가 달라졌는지 알 수 있다. 소리를 낸 물체가 화면에 보이지 않을 수도 있다. 두 명령에 `--media`를 붙이면 움직이는 화면과 원래 채널의 PCM 소리를 파일 하나로 재생할 수 있다. [구간별 출력 읽는 법](docs/READING.md)

`--until 8.5`를 주면 처음 8.5초만 잘라 분석하고 화면을 확대하거나 원음과 프레임을 꺼낼 때도 그 구간에서 읽는다.
각 시점의 결과는 새 폴더나 빈 폴더에 저장한다.

화면과 소리를 함께 살피는 `reel observe`는 프레임 시트, 원래 채널을 보존한 WAV, 프레임별 JSON을 돌려준다. JSON에는 각 화면이 떠 있던 동안 재생된 오디오 샘플의 범위와 채널별 음량이 들어간다. 소리 분류 모델이 없어도 쓸 수 있다.

첫 반응을 보려면 페이지와 `stream.md`를 순서대로 건네고 마지막에 `now.png`가 있으면 함께 보여준다.
이 그림에는 멈추기 직전의 화면이 원본 크기로 담기며, 영상이 끝나고 소리만 이어지면 생략된다.
"방금 무엇을 알아차렸어요?"라고 묻고 첫 답을 들은 뒤에 궁금한 장면을 짚는다. [닫힌 문으로 시험한 결과](docs/door-trial.md)

빠른 동작은 `reel frame`에 시작과 끝 시각을 주면 프레임마다 볼 수 있다. JSON에 적힌 PNG를 순서대로 열면 각 칸 아래에 그 화면이 처음 뜬 시각이 나온다. 시작 시각에 이미 보이던 프레임도 들어가며 같은 프레임이 오래 떠 있으면 한 번만 나온다. 끝 시각에 새로 뜬 프레임은 다음 구간에 속한다. 특정 시각의 화면을 원본 해상도로 볼 때는 `--at`을 쓴다.

이미지와 텍스트를 읽을 수 있는 모델에는 같은 페이지 PNG와 `stream.md`의 해당 부분, `reel listen`이 출력한 JSON을 건네면 된다. 셸을 쓸 수 있는 모델은 궁금한 구간을 직접 지정할 수 있고, 오디오를 입력받는 모델에는 그 구간의 WAV 파일도 건넬 수 있다. JSON에는 형식 이름 `reel.listen.v1`, 로컬 WAV 경로, 채널별 음량과 소리가 커진 시각이 들어간다. AudioSet 가중치가 있으면 소리의 후보도 나온다.

소리의 출처를 더 알아보려면 `reel setup --compare-sounds`를 한 번 실행하고 `reel listen`에 `--compare-sounds`를 붙인다. 로컬 CLAP 모델이 527개 소리 설명과 비교해 구간마다 가장 가까운 세 후보를 돌려준다. 이 점수는 소리의 유사도를 나타내므로 생소한 소리는 세 후보 모두 빗나갈 수 있다. 대사를 받아쓸 근거는 별도로 필요하다. 별도 파이썬 환경에 `tags`를 설치했다면 `REEL_COMPARE_PYTHON`으로 지정한다.

원음을 듣거나 화면과 함께 읽으려면 `reel setup --describe-sounds`를 실행한다. 필요한 경우 별도 파이썬 환경을 만들고 Qwen3-Omni 8비트 가중치(약 39GB)를 받는다. 64GB Apple Silicon Mac에서 실행했다.

이미 `hearing`을 설치한 환경이 있으면 `REEL_HEAR_PYTHON`으로 그 환경의 파이썬을 지정할 수 있다.

`reel play`나 `reel listen`에 `--describe-sounds`를 붙이면 모델이 WAV를 듣고 적은 묘사와 채널별 측정값이 JSON에 들어간다. 모델이 소리의 출처를 잘못 짚기도 하므로 대사를 인용할 때는 발화 기록을 확인한다.

Synchformer를 설치하면 `reel observe`에 `--sync-region LEFT TOP RIGHT BOTTOM`을 붙여 지정한 영역의 움직임과 소리의 시점을 비교할 수 있다. 좌표는 왼쪽 위 (0, 0)에서 오른쪽 아래 (1, 1)까지이며 영역마다 같은 녹음에 대한 점수가 나온다. 화면 밖에서 난 소리도 동작과 우연히 때가 맞으면 높은 점수를 받을 수 있다. [측정값 읽는 법](docs/READING.md)

그다음 `reels/film/pages/`를 [docs/READING.md](docs/READING.md)에 따라 순서대로 읽는다. M4 Pro 기준 10분 영상에 싱크 측정을 포함해 10분쯤 걸린다.

먼저 보고 싶으면 `python examples/make_demo.py examples/out/demo.mp4 --music 쓸-수-있는-음악.wav`로
위 그림의 70초 예시 영상을 만들 수 있다. 문제 여섯 개가 일부러 심어져 있다.

분리 모델이 따로 가상환경을 써야 하면 `REEL_SEP_PYTHON`, `REEL_CROWD_PYTHON`, `REEL_ASR_PYTHON`, `REEL_GAZE_PYTHON`으로
지정한다. 모델과 캐시는 `~/.cache/reel`(`REEL_HOME`)에 둔다.

## 한계

- 함성 속에서 소리치는 해설은 여전히 받아쓰지 못할 때가 많다. 반복은 표시되지만 그 말은 비어 있을 수 있다.
- 구호의 말은 화면에 나온 이름과 장르에서 흔한 이름 중 발음이 가까운 것으로 추정한다. 화면에 한 번도 안 나온 이름은 잘못 들을 수 있다.
- 시선은 시선 추적 데이터로 학습한 모델이 내놓은 예측값이다. 누가 주인공인지도 모른다.
- 싱크는 화면에 보이는 입, 타격, 발걸음으로 읽는다. 내레이션, 풍경 위 음악, 작은 웹캠 얼굴, 빠른 몽타주에서는 읽을 단서가 적고 애니메이션에서는 판단이 약하다("확실" 대신 "아마").
- 원래 거의 움직이지 않는 장면의 정지, 이미 조용한 구간의 소리 끊김, 활발한 말소리 속 볼륨 점프는 아직 자주 놓친다.
- 소리 분류는 무슨 소리 같은지(찰싹, 사이렌, 자동차)를 알려줄 뿐 음악이 좋은지는 판단하지 않는다. 음악이 싸 보이는지는 읽는 쪽이 판단한다.
- 부주의한 독자는 `meta.json`이나 `stream.md`를 미리 볼 수 있다. 위 시험에서 두 독자가 앞부분을 잠깐 봤다고 밝혔다. 판단에는 쓰지 않았다고 했다.
- Reel은 보여줄 뿐 취향을 정하지 않는다.

## 사용한 오픈소스

낭독 비교에는 Google FLEURS의 벵골어, 영어, 힌디어, 한국어, 중남미 스페인어, 태국어 시험 자료를 썼다. 녹음과 기준 문장의 이용 조건은 CC BY 4.0이다. [녹음과 기준 문장](https://huggingface.co/datasets/google/fleurs)

사람과 GPT가 같은 발화를 들은 실험에는 Magnotti 등(2024)이 CC0으로 공개한 영상과 연구진이 집계한 참가자 응답을 썼다. [실험 영상](https://openwetware.org/wiki/Beauchamp:Stimuli), [연구진의 분석 자료](https://zenodo.org/records/10689633)

발음 비교 실험에는 Meta가 Apache-2.0으로 공개한 XLSR 음소 인식기를 썼다. [모델 카드](https://huggingface.co/facebook/wav2vec2-xlsr-53-espeak-cv-ft), [발음 비교 결과](docs/perception-check.md#같은-발화를-들은-사람-28명과-gpt)

- 시선: [UNISAL](https://github.com/rdroste/unisal)(Apache-2.0)
- 소리 분리: [Demucs](https://github.com/facebookresearch/demucs)(MIT), [audio-separator](https://github.com/nomadkaraoke/python-audio-separator)(MIT)의 Mel-Band Roformer 관중 모델(aufr33, viperx)
- 발화 구간 검출: [Silero VAD](https://github.com/snakers4/silero-vad)(MIT)
- 받아쓰기: [Whisper](https://github.com/openai/whisper)(MIT)를 [mlx-whisper](https://github.com/ml-explore/mlx-examples)와 [faster-whisper](https://github.com/SYSTRAN/faster-whisper)로 실행
- 화면 글자: [ocrmac](https://github.com/straussmaximilian/ocrmac)로 Apple Vision 사용
- 싱크: [Synchformer](https://github.com/v-iashin/Synchformer)(MIT)
- 소리 분류: [AST AudioSet 모델](https://huggingface.co/MIT/ast-finetuned-audioset-10-10-0.4593)(BSD-3)
- 소리 설명과의 유사도 비교에는 LAION의 [CLAP HTSAT 모델](https://huggingface.co/laion/clap-htsat-unfused)(Apache 2.0)을 쓴다.

점 추적에는 Google DeepMind가 Apache-2.0으로 공개한 온라인 BootsTAPIR 코드와 가중치를 쓴다. [코드와 가중치](https://github.com/google-deepmind/tapnet)

겹친 소리를 다시 들을 때는 Google Research가 YFCC100M으로 학습한 MixIT 모델을 쓴다. 연구진이 배포한 가중치의 라이선스는 Apache-2.0이다. [모델과 라이선스](https://github.com/google-research/sound-separation/tree/master/models/neurips2020_mixit#yfcc100m-model-checkpoint)

청취 비교 실험에는 Google의 Gemma 4 E4B-IT를 mlx-community가 4비트로 변환한 모델도 썼다. 원본 모델의 라이선스는 Apache-2.0이다. [Google 모델 카드](https://huggingface.co/google/gemma-4-E4B-it), [MLX 변환본](https://huggingface.co/mlx-community/gemma-4-e4b-it-4bit)

선택형 원음 묘사는 Qwen 팀의 Qwen3-Omni와 mlx-community의 8비트 MLX 변환본을 쓴다. 모델 카드의 라이선스는 Apache-2.0이다. [원본 모델](https://huggingface.co/Qwen/Qwen3-Omni-30B-A3B-Instruct), [MLX 가중치](https://huggingface.co/mlx-community/Qwen3-Omni-30B-A3B-Instruct-8bit)
- 그 밖에 librosa, OpenCV, PyTorch

상업적으로 쓰기 전에 모델마다 라이선스를 확인할 것.

예시 그림의 음악은 Kevin MacLeod(incompetech.com)의 "Crypto"(Creative Commons Attribution 4.0)이다. 내레이션은 macOS `say`로 만들었다.

사람이 소리만 들었을 때와 화면까지 봤을 때 쓴 설명은 Sudarsanam, Martín Morató, Hakala, Virtanen의 AVCaps에서 가져왔다. 저자들이 CC BY-NC 4.0으로 공개한 자료를 비상업 연구에 사용했다. [AVCaps 자료와 이용 조건](https://huggingface.co/datasets/TUT-ARG/AVCaps), [실험 결과](docs/listening-check.md#사람들이-낯선-영상을-한-문장으로-설명한-경우)

음높이 측정에는 Bittner, Pasalo, Bosch, Meseguer-Brocal, Rubinstein이 CC BY 4.0으로 공개한 독창 녹음 40개와 음악가의 주석을 썼다. [vocadito 녹음과 주석](https://zenodo.org/records/5578807)

영상 설명을 비교하는 `examples/check_first_impressions.py`에서는 Apache-2.0으로 공개된 all-MiniLM-L6-v2로 문장을 벡터로 바꾼다. [문장 모델과 사용법](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2)

발화 검증에는 [ESC-10](https://github.com/karolpiczak/ESC-50)(CC BY 3.0)과 [Free Spoken Digit Dataset](https://github.com/Jakobovski/free-spoken-digit-dataset)(CC BY-SA 4.0)의 녹음을 썼다.

두 소리를 겹쳐 듣는 실험에는 Karol J. Piczak이 공개한 ESC-50(CC BY-NC 3.0)의 50개 분류에서 녹음을 한 편씩 썼다. [자료 이용 조건과 녹음별 출처](https://github.com/karolpiczak/ESC-50/blob/master/LICENSE)

화면과 소리를 검증할 때는 Google DeepMind의 Perception Test에서 공개한 영상과 문항, 소리의 출처가 화면에 보이는지를 기록한 주석을 썼다. [Perception Test, CC BY 4.0](https://github.com/google-deepmind/perception_test)
입력에 따라 GPT가 무엇을 맞히고 어디서 답을 보류했는지는 [화면 검증 기록](docs/perception-check.md)에 정리했다.

처음 듣는 소리를 묘사할 때는 Étienne Labbé, Thomas Pellegrini, Julien Pinquier의 CoNeTTE 모델(MIT)을 쓴다. [모델과 한계](https://huggingface.co/Labbeti/conette), [구현](https://github.com/Labbeti/conette-audio-captioning)

현장 녹음 실험에는 Lipping, Sudarsanam, Drossos, Virtanen이 공개한 Clotho-AQA의 질문과 답(MIT) 및 Clotho v2.1의 소리 묘사를 썼다. 녹음마다 Freesound 원작자가 정한 이용 조건이 있고 소리 묘사에는 탐페레 대학교의 이용 조건이 적용된다. [Clotho-AQA](https://zenodo.org/records/6473207), [Clotho v2.1](https://zenodo.org/records/4783391)

화자의 설명과 화면 위치를 비교할 때는 Pont-Tuset, Uijlings, Changpinyo, Soricut, Ferrari의 Localized Narratives에서 공개한 녹음과 시각별 문장, 마우스 좌표(CC BY 4.0)를 COCO 검증 사진과 함께 사용했다. [Localized Narratives](https://google.github.io/localized-narratives/), [COCO 이용 조건](https://cocodataset.org/#termsofuse)

원음 청취 실험에서 Gemma 4 E2B IT를 시험했다. 원 모델 카드는 Apache 2.0, 실험에 쓴 MLX 변환본은 Gemma 라이선스를 표기한다. [Google 모델 카드](https://huggingface.co/google/gemma-4-E2B-it), [MLX 변환본](https://huggingface.co/mlx-community/gemma-4-e2b-it-4bit)

표지판을 읽는 실험에는 COCO 사진과 COCO-Text V2의 전사를 썼다. 사진마다 원작자가 정한 이용 조건이 있다. [COCO-Text V2](https://bgshih.github.io/cocotext/), [COCO 사진 이용 조건](https://cocodataset.org/dataset/termsofuse.htm)

영상을 자유롭게 보는 사람들의 시선을 비교할 때는 Mital, Smith, Hill, Henderson의 DIEM 연구 자료를 썼다. 비상업적 연구에 CC BY-NC-SA 3.0 조건으로 사용할 수 있다. [DIEM 자료와 이용 조건](https://jhenderson.org/vclab/DIEM.html)

헤드폰 방향 검증에는 Wierstorf, Spors, Raake가 공개한 Two!Ears 실험 자료(CC BY 4.0)로 정면을 향한 머리의 양쪽 귀에 도달하는 소리를 재현해 참가자들이 가리킨 방향과 비교했다. 화면 속 소리의 출처를 찾는 실험에는 SSL-SaN 저자들이 공개한 가중치와 MIT 라이선스 코드를 썼다. [Two!Ears 자료와 이용 조건](https://docs.twoears.eu/en/latest/database/listening-tests/localisation/), [SSL-SaN](https://github.com/xavijuanola/SSL_SaN)

자동으로 소리의 위치를 찾는 실험에는 SLAVC 저자들이 VGG-Sound 144k로 학습한 모델과 Apache-2.0으로 공개한 구현을 썼다. [SLAVC 논문, 코드, 가중치](https://github.com/stoneMo/SLAVC)

청각 실험에는 탐페레 대학교 Machine Listening Group의 MACS 주석과 탐페레 공과대학교 Audio Research Group의 TAU Urban Acoustic Scenes 2019 녹음을 비상업 실험용 라이선스로 사용했다. [청각 검증 기록](docs/listening-check.md), [MACS](https://zenodo.org/records/5114771), [TAU 녹음](https://zenodo.org/records/2589280)

## 라이선스

MIT
