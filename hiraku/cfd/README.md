# hiraku.cfd — GPU の流体計算

設計の形を入れると、GPU の流体計算で空気抵抗と横風の 6 分力を出すパッケージ。外から使うのは `hiraku/tools/cfd_tools.py` (ツール `cfd_aero`) だけで、`hiraku/tools/mcp.py` の 1 行がそれを読み込む。このフォルダ・アダプタ・その 1 行を消しても、ほかは動く (`tests/cfd/test_coupling.py` が確かめる)。

ここで出す値は、実験と比べて確かめたものではない。記録の `physically_validated` は false で、`assumptions` に「確かめていない」と書く。既定の Cd 0.8・Cy 1.0 は置き換えない。

## 解き方

- D3Q19 の格子ボルツマン法。2 枚の配列の pull 方式、32 ビット浮動小数点、分布は `h = f - w` で持つ (Lehmann et al., PRE 106, 015308, 2022)
- 衝突は正則化 (Latt & Chopard, 2006) と Smagorinsky のモデル。壁関数は地面だけで、車体の壁には無い
- 力は全ステップの運動量交換の和の差で出す。瞬間の値は奇数と偶数のステップで振れるので、結果には使わない。ゼロの状態の重みは力に入れない (地面に立つ車輪の接地面で、基準の圧力の分の偽の荷重になるため)
- 形は凸の立体の集まり (箱・管・円柱・球)。格子の線と立体の交わりで線を切るので、節点の中心が入らない 3 mm の板や細い管も、両側から流れを止める。壁の位置は線の上の距離で補間する (Bouzidi et al., 2001)。外の空気が届かない節点は固体にする。網は多孔の面として扱う
- 周りは一様流の遠方の箱で、内側に吸収層を置く。周期の境界は使わない。地面は滑る面に壁関数の応力を足し、動く地面は応力の差で効く。車輪は回転する壁の速度を持つ (節点ごとに質量を足さない形)。タイヤと地面のすき間が 1.5 格子より狭い所は箱で埋める
- 横風は空気の向きを格子の +x に固定して車を回す。車の前後軸に沿って地面が動く

## GPU

Python 標準の ctypes で、ドライバの CUDA (`nvcuda.dll`、`libcuda.so.1`) を呼ぶ。依存は増えない。GPU の無い環境ではツールが「GPU が無いので回さない」と答え、試験は理由つきで skip する。CPU では回さない。保存した記録の再生は GPU 無しで動く。

走らせる前に `nvidia-smi` の空きを測り、配列と余裕 600 MiB が入らなければ断る (`solver.check_memory`)。1 度に 1 本で、`cuda.gpu_turn()` が先着順の列を作る。

### kernels.ptx の作り直し

`kernels.ptx` は `kernels.cu` から作った生成物で、先頭の行に元のファイルの sha256 (改行の違いを除く) を書いてある。NVIDIA のドライバがあれば PTX をそのまま読めるので、実行に NVRTC は要らない。`kernels.cu` を変えたら作り直す:

```
python -m hiraku.cfd.cuda
python -m hiraku.cfd.cuda water
```

コンパイラは nvcc ではなく NVRTC (`nvrtc64_120_0.dll`、NVIDIA の `nvidia-cuda-nvrtc` 12.9 に入っている物)。探す順は、環境変数 `HIRAKU_NVRTC_DLL`、`PATH`、Python の site-packages。オプションは `--gpu-architecture=compute_75 -std=c++17`。古い印の PTX を読もうとしたときも、同じ手順で作り直す (NVRTC が無ければ、理由つきで止まる)。
