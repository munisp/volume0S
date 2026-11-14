# NVIDIA's CUDA 13.0 PyTorch stack for ARM (GB10), as creators-image.
FROM nvcr.io/nvidia/vllm:25.09-py3
RUN pip freeze | grep -iE "^(torch|torchvision|numpy|transformers|accelerate|huggingface[-_]hub|tokenizers|safetensors)==" > /tmp/keep.txt \
 && pip install --no-cache-dir --constraint /tmp/keep.txt "diffusers==0.35.2" accelerate ftfy sentencepiece protobuf imageio imageio-ffmpeg \
    "fastapi==0.141.1" "starlette==1.7.0" "uvicorn>=0.34"
WORKDIR /srv
COPY server.py .
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8892"]
