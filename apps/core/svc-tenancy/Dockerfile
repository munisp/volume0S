FROM golang:1.23-alpine AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go test ./... && CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /bin/svc-tenancy .

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /bin/svc-tenancy /svc-tenancy
USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["/svc-tenancy"]
