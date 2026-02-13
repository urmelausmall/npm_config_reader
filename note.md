docker run --privileged --rm tonistiigi/binfmt --install all

docker buildx create --use

docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t urmelausmall/npm_config_reader:latest \
  -t urmelausmall/npm_config_reader:0.95 \
  --push \
  .
