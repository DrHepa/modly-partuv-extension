# Native release blocker: Windows and ARM64

The portable process wrapper is not the native PartUV library. This delivery
contains no fabricated wheel URL, placeholder binary, automatic WSL dependency
or replacement unwrap algorithm.

## Confirmed upstream issues to address

The reviewed CMake project declares CUDA, hard-codes GPU architectures
`80 89 90 120`, falls back to a Unix nvcc path, uses GCC/x86-specific optimization
flags and `-fPIC`, finds an easy_profiler shared object and applies `$ORIGIN`
RPATHs. It also depends on CGAL, yaml-cpp, OpenMP, TBB and other geometry headers.
These choices cannot simply be copied into a native Windows or ARM64 setup.

## Required port/build work (NOT completed)

1. Make compiler flags, shared-library handling and runtime lookup conditional
   on platform/compiler. Audit sources for actual portability problems.
2. Make CUDA architectures a user/build-matrix input. Include every GPU family
   advertised by the release, and run real PAMO kernels on that family.
3. Build/resolve redistributable dependency binaries and audit their licenses;
   do not assume the wrapper's license covers native dependencies.
4. Produce cp311 and cp312 Windows wheels and separate Linux ARM64 wheels.
   Build in controlled toolchains; do not use `-march=native` in portable assets.
5. Audit each binary's dependent DLLs/shared objects and wheel platform tags.
   Resolve redistributables in the extension runtime, not a developer's machine.
6. Run full hierarchy → unwrap → pack → rebake fixtures on supported hardware.
   An `import partuv` success or a torch-scatter test is insufficient.
7. Publish checksummed binaries only after native acceptance, then add their
   exact URLs/hashes to the installer lockfile and remove the missing-wheel gate.

The preview accepts an explicitly supplied, SHA256-checked local developer wheel
for Windows, but that is a testing hook, not an implemented Windows port.
ARM64 is gated even with a custom PartUV wheel because its Torch/scatter/other
binary dependency lane has not been specified and validated.

## Upstream host issues separate from the PartUV port

No new Modly data type is required for the main node. Native process transport
currently exposes one filePath and one text result. A true two-mesh transfer
node would benefit from explicit multiple mesh slots. Python process UI
cancellation should be fixed in Modly's PythonProcessRunner (track child
processes, terminate process trees, reject pending runs). No host patch has
been applied or published as part of this delivery.
