# syntax=docker/dockerfile:1.7
# ApoL1 bispecific programme - Boltz-2 co-folding image
# ghcr.io/mjcipriano/apol1-boltz:2.2.1
#
# Build (weights downloaded from the official Boltz release on Hugging Face, sha256-verified):
#   docker build -t ghcr.io/mjcipriano/apol1-boltz:2.2.1 .
# Build re-using an existing ~/.boltz cache (put/hardlink boltz2_conf.ckpt, boltz2_aff.ckpt, mols.tar in ./weights/):
#   docker build --build-arg WEIGHTS_SOURCE=local -t ghcr.io/mjcipriano/apol1-boltz:2.2.1 .

ARG CUDA_TAG=12.4.1-base-ubuntu22.04
ARG WEIGHTS_SOURCE=download

# ---------------------------------------------------------------- weights (download)
FROM nvidia/cuda:${CUDA_TAG} AS weights-download
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*
COPY weights.sha256 /tmp/weights.sha256
RUN mkdir -p /root/.boltz && cd /root/.boltz \
 && for f in boltz2_conf.ckpt boltz2_aff.ckpt mols.tar; do \
      curl -fL --retry 5 --retry-delay 10 -o "$f" "https://huggingface.co/boltz-community/boltz-2/resolve/main/$f"; \
    done \
 && sha256sum -c /tmp/weights.sha256

# ---------------------------------------------------------------- weights (local copy)
FROM nvidia/cuda:${CUDA_TAG} AS weights-local
COPY weights.sha256 /tmp/weights.sha256
COPY weights/boltz2_conf.ckpt weights/boltz2_aff.ckpt weights/mols.tar /root/.boltz/
RUN cd /root/.boltz && sha256sum -c /tmp/weights.sha256

# ---------------------------------------------------------------- weights (common post-processing)
FROM weights-${WEIGHTS_SOURCE} AS weights
# Extract the CCD molecule pickles. boltz 2.2.1 (src/boltz/main.py download_boltz2) only tests that
# mols.tar *exists* once mols/ is present, so the 1.8 GB tarball is replaced by a 0-byte sentinel to
# avoid shipping the CCD twice. `boltz predict --cache /root/.boltz` therefore never downloads anything.
RUN cd /root/.boltz && tar -xf mols.tar && rm -f mols.tar && : > mols.tar \
 && test "$(ls mols | wc -l)" -ge 45000 && ls -la /root/.boltz

# ---------------------------------------------------------------- runtime
FROM nvidia/cuda:${CUDA_TAG}
LABEL org.opencontainers.image.source="https://github.com/mjcipriano/apol1-boltz" \
      org.opencontainers.image.description="Boltz-2 2.2.1 + weights/CCD + ApoL1-programme batch scripts (scFv x antigen co-folding)" \
      org.opencontainers.image.licenses="MIT"
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    MAMBA_ROOT_PREFIX=/opt/micromamba PATH=/opt/py/bin:/usr/local/bin:/usr/bin:/bin:/usr/local/cuda/bin
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl bzip2 git procps \
 && rm -rf /var/lib/apt/lists/*

# Python 3.11 via micromamba (pinned release), then pip for the ML stack
ARG MICROMAMBA_VERSION=2.0.5-0
RUN curl -fsSL -o /usr/local/bin/micromamba \
      "https://github.com/mamba-org/micromamba-releases/releases/download/${MICROMAMBA_VERSION}/micromamba-linux-64" \
 && chmod +x /usr/local/bin/micromamba \
 && micromamba create -y -p /opt/py -c conda-forge python=3.11 pip \
 && micromamba clean -a -y

# torch cu124 wheels bundle their own CUDA runtime libraries (nvidia-* pip packages), so the slim CUDA
# base image is sufficient; only the host driver (libcuda) is injected by nvidia-container-toolkit/Modal.
ARG TORCH_VERSION=2.5.1
ARG TORCH_INDEX=https://download.pytorch.org/whl/cu124
ARG BOLTZ_VERSION=2.2.1
RUN pip install --no-cache-dir "torch==${TORCH_VERSION}" --index-url "${TORCH_INDEX}" \
 && pip install --no-cache-dir "boltz==${BOLTZ_VERSION}" \
 && (pip check || true) \
 && find /opt/py -depth -type d -name __pycache__ -exec rm -rf {} + \
 && find /opt/py -depth -type d -name tests -path '*site-packages*' -exec rm -rf {} + || true

# Boltz-2 weights + CCD, verified in the weights stage. World-readable (and /root traversable) so the
# container can run as an unprivileged user (--user $(id -u):$(id -g)) and bind-mounted outputs stay user-owned.
COPY --from=weights --chmod=755 /root/.boltz /root/.boltz
RUN chmod 755 /root

# Programme scripts
COPY scripts/ /opt/apol1/
RUN chmod +x /opt/apol1/*.py && python -c "import boltz, torch; print('boltz ok, torch', torch.__version__)"

WORKDIR /work
RUN chmod 1777 /work
ENV BOLTZ_CACHE=/root/.boltz
CMD ["python", "/opt/apol1/boltz_batch.py"]
