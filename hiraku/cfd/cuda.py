"""CUDA through ctypes: the Driver API, and NVRTC only to rebuild the kernels.

No package is needed: the driver's own library is loaded when a GPU is used. The kernels run from
kernels.ptx (text, built from kernels.cu and stamped with its hash), so a PC with an NVIDIA driver needs
nothing else. On a PC without a GPU, GpuUnavailable says why and nothing runs.
"""

from __future__ import annotations

import ctypes as ct
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

HERE = Path(__file__).resolve().parent
STAMP = "// built from kernels.cu sha256 "

CU_CTX_SCHED_BLOCKING_SYNC = 2  # wait without spinning a CPU core


class GpuUnavailable(RuntimeError):
    """No usable GPU here: the message says why, for the tool's answer and the skipped tests."""


class CudaError(RuntimeError):
    """A CUDA call failed."""


def _load_driver() -> ct.CDLL:
    name = "nvcuda.dll" if sys.platform == "win32" else "libcuda.so.1"
    try:
        return ct.CDLL(name)
    except OSError as e:
        raise GpuUnavailable(f"NVIDIA のドライバの CUDA ({name}) がありません: {e}") from None


def source_hash(name: str = "kernels") -> str:
    return hashlib.sha256((HERE / f"{name}.cu").read_bytes()).hexdigest()


def _nvrtc_candidates() -> Iterable[Path]:
    env = os.environ.get("HIRAKU_NVRTC_DLL")
    if env:
        yield Path(env)
    names = ("nvrtc64_120_0.dll", "nvrtc64_130_0.dll", "nvrtc64_112_0.dll") if sys.platform == "win32" else ("libnvrtc.so",)
    for name in names:
        found = shutil.which(name)
        if found:
            yield Path(found)
    for prefix in {sys.prefix, sys.base_prefix}:
        site = Path(prefix) / ("Lib/site-packages" if sys.platform == "win32" else "lib")
        for name in names:
            yield site / "nvidia" / "cuda_nvrtc" / "bin" / name
            yield site / "torch" / "lib" / name


def compile_ptx(source: str) -> str:
    """PTX text of a kernel source, by NVRTC (only needed when kernels.cu changes)."""
    lib = None
    for path in _nvrtc_candidates():
        if path.is_file():
            # NVRTC loads its builtins library by name at compile time, from the search path
            os.environ["PATH"] = str(path.parent) + os.pathsep + os.environ.get("PATH", "")
            lib = ct.CDLL(str(path))
            break
    if lib is None:
        raise GpuUnavailable("NVRTC が見つからないので kernels.cu を組み直せません (HIRAKU_NVRTC_DLL で指せます)")
    prog = ct.c_void_p()

    def check(code: int, what: str) -> None:
        if code != 0:
            size = ct.c_size_t()
            lib.nvrtcGetProgramLogSize(prog, ct.byref(size))
            log = ct.create_string_buffer(size.value + 1)
            lib.nvrtcGetProgramLog(prog, log)
            raise CudaError(f"{what}: NVRTC error {code}\n{log.value.decode(errors='replace')}")

    src = source.encode()
    check(lib.nvrtcCreateProgram(ct.byref(prog), ct.c_char_p(src), b"kernels.cu", 0, None, None), "create")
    opts = (ct.c_char_p * 2)(b"--gpu-architecture=compute_75", b"-std=c++17")
    check(lib.nvrtcCompileProgram(prog, 2, opts), "compile")
    size = ct.c_size_t()
    check(lib.nvrtcGetPTXSize(prog, ct.byref(size)), "ptx size")
    buf = ct.create_string_buffer(size.value)
    check(lib.nvrtcGetPTX(prog, buf), "ptx")
    lib.nvrtcDestroyProgram(ct.byref(prog))
    return buf.value.decode()


def build_ptx(name: str = "kernels") -> Path:
    """Rebuild <name>.ptx from <name>.cu."""
    path = HERE / f"{name}.ptx"
    ptx = compile_ptx((HERE / f"{name}.cu").read_text(encoding="utf-8"))
    path.write_text(f"{STAMP}{source_hash(name)}\n{ptx}", encoding="utf-8", newline="\n")
    return path


def kernels_ptx(name: str = "kernels") -> str:
    """The PTX for the current <name>.cu; rebuilt by NVRTC when the stamp is stale."""
    path = HERE / f"{name}.ptx"
    if path.is_file():
        text = path.read_text(encoding="utf-8")
        if text.startswith(STAMP + source_hash(name)):
            return text
    build_ptx(name)
    return path.read_text(encoding="utf-8")


def gpu_memory_mib() -> dict[str, int] | None:
    """Total, used and free MiB as nvidia-smi sees them (all processes), or None when it cannot say."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--query-gpu=memory.total,memory.used,memory.free", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=20).stdout
        total, used, free = (int(float(x)) for x in out.splitlines()[0].split(","))
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None
    return {"total": total, "used": used, "free": free}


@contextmanager
def gpu_turn(wait_s: float = 86400.0) -> Iterator[None]:
    """One flow run on the GPU at a time, across processes. The OS drops the lock if the process dies."""
    path = Path(tempfile.gettempdir()) / "hiraku-cfd-gpu.lock"
    f = open(path, "a+b")
    t0 = time.monotonic()
    try:
        while True:
            try:
                f.seek(0)
                if sys.platform == "win32":
                    import msvcrt

                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() - t0 > wait_s:
                    raise TimeoutError("GPU の順番が回ってきませんでした") from None
                time.sleep(2.0)
        yield
    finally:
        try:
            f.seek(0)
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        f.close()


class Buffer:
    """A device allocation."""

    def __init__(self, gpu: "Gpu", size: int):
        self.gpu = gpu
        self.size = int(size)
        self.ptr = ct.c_ulonglong()
        gpu.check(gpu.lib.cuMemAlloc_v2(ct.byref(self.ptr), ct.c_size_t(self.size)), f"cuMemAlloc {self.size} bytes")
        gpu.allocated += self.size

    def write(self, array: Any, offset: int = 0) -> None:
        data = array.tobytes() if hasattr(array, "tobytes") else bytes(array)
        self.gpu.check(self.gpu.lib.cuMemcpyHtoD_v2(ct.c_ulonglong(self.ptr.value + offset), data, ct.c_size_t(len(data))), "cuMemcpyHtoD")

    def read_into(self, array: Any, offset: int = 0) -> None:
        import numpy as np

        view = np.asarray(array)
        ptr = view.ctypes.data_as(ct.c_void_p)
        self.gpu.check(self.gpu.lib.cuMemcpyDtoH_v2(ptr, ct.c_ulonglong(self.ptr.value + offset), ct.c_size_t(view.nbytes)), "cuMemcpyDtoH")

    def zero(self) -> None:
        self.gpu.check(self.gpu.lib.cuMemsetD8_v2(self.ptr, ct.c_ubyte(0), ct.c_size_t(self.size)), "cuMemsetD8")

    def free(self) -> None:
        if self.ptr.value:
            self.gpu.check(self.gpu.lib.cuMemFree_v2(self.ptr), "cuMemFree")
            self.gpu.allocated -= self.size
            self.ptr = ct.c_ulonglong()


class Launch:
    """A kernel with its arguments held ready; changing a held ctypes value changes the next launch."""

    def __init__(self, gpu: "Gpu", func: ct.c_void_p, args: Iterable[Any], grid: tuple[int, int, int], block: tuple[int, int, int]):
        self.gpu = gpu
        self.func = func
        self.args = list(args)
        self.params = (ct.c_void_p * len(self.args))(*[ct.cast(ct.pointer(a), ct.c_void_p) for a in self.args])
        self.dims = [ct.c_uint(v) for v in (*grid, *block)]

    def __call__(self) -> None:
        d = self.dims
        code = self.gpu.lib.cuLaunchKernel(self.func, d[0], d[1], d[2], d[3], d[4], d[5], ct.c_uint(0), None, self.params, None)
        if code != 0:
            raise CudaError(f"cuLaunchKernel: CUDA error {code}")


class Gpu:
    """One CUDA context on the first device, with the kernels <kernels>.cu loaded."""

    def __init__(self, kernels: str = "kernels") -> None:
        self.lib = _load_driver()
        code = self.lib.cuInit(0)
        if code != 0:
            raise GpuUnavailable(f"CUDA を始められません (cuInit {code}。GPU が無いか、ドライバが使えません)")
        count = ct.c_int()
        self.check(self.lib.cuDeviceGetCount(ct.byref(count)), "cuDeviceGetCount")
        if count.value < 1:
            raise GpuUnavailable("CUDA の使える GPU がありません")
        self.device = ct.c_int()
        self.check(self.lib.cuDeviceGet(ct.byref(self.device), 0), "cuDeviceGet")
        self.ctx = ct.c_void_p()
        self.check(self.lib.cuCtxCreate_v2(ct.byref(self.ctx), CU_CTX_SCHED_BLOCKING_SYNC, self.device), "cuCtxCreate")
        name = ct.create_string_buffer(128)
        self.check(self.lib.cuDeviceGetName(name, 128, self.device), "cuDeviceGetName")
        self.name = name.value.decode()
        version = ct.c_int()
        self.lib.cuDriverGetVersion(ct.byref(version))
        self.cuda_version = version.value
        self.allocated = 0
        self.module = ct.c_void_p()
        ptx = kernels_ptx(kernels).encode()
        log = ct.create_string_buffer(8192)
        opt = (ct.c_int * 2)(5, 6)  # CU_JIT_ERROR_LOG_BUFFER, CU_JIT_ERROR_LOG_BUFFER_SIZE_BYTES
        val = (ct.c_void_p * 2)(ct.cast(log, ct.c_void_p), ct.c_void_p(8192))
        code = self.lib.cuModuleLoadDataEx(ct.byref(self.module), ct.c_char_p(ptx), 2, opt, val)
        if code != 0:
            raise CudaError(f"cuModuleLoadDataEx {code}: {log.value.decode(errors='replace')}")

    def check(self, code: int, what: str) -> None:
        if code != 0:
            raise CudaError(f"{what}: CUDA error {code}")

    def kernel(self, name: str) -> ct.c_void_p:
        fn = ct.c_void_p()
        self.check(self.lib.cuModuleGetFunction(ct.byref(fn), self.module, name.encode()), f"cuModuleGetFunction {name}")
        return fn

    def buffer(self, size: int) -> Buffer:
        return Buffer(self, size)

    def launch(self, name: str, args: Iterable[Any], grid: tuple[int, int, int], block: tuple[int, int, int]) -> Launch:
        return Launch(self, self.kernel(name), args, grid, block)

    def sync(self) -> None:
        self.check(self.lib.cuCtxSynchronize(), "cuCtxSynchronize")

    def close(self) -> None:
        if self.ctx:
            self.lib.cuCtxDestroy_v2(self.ctx)
            self.ctx = ct.c_void_p()

    def __enter__(self) -> "Gpu":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def gpu_or_reason(kernels: str = "kernels") -> tuple[Gpu | None, str]:
    """A ready Gpu, or None and the reason in Japanese (for the tool and the skipped tests)."""
    try:
        return Gpu(kernels), ""
    except GpuUnavailable as e:
        return None, str(e)


if __name__ == "__main__":
    print(build_ptx(sys.argv[1] if len(sys.argv) > 1 else "kernels"))
