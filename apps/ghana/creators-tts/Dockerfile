# NVIDIA's CUDA 13.0 PyTorch stack for ARM (GB10); see server.py for why this version.
FROM nvcr.io/nvidia/vllm:25.09-py3
RUN apt-get update && apt-get install -y --no-install-recommends espeak-ng && rm -rf /var/lib/apt/lists/*
RUN pip freeze | grep -iE "^(torch|torchaudio|numpy)==" > /tmp/keep.txt \
 && pip install --no-cache-dir --constraint /tmp/keep.txt "kokoro>=0.9.4,<1" "soundfile>=0.12" "fastapi==0.141.1" "starlette==1.7.0" "uvicorn>=0.34"
WORKDIR /srv
COPY server.py .
ENV HF_HOME=/models
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8880"]
