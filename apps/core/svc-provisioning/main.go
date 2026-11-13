package main

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"
)

func main() {
	slog.SetDefault(slog.New(slog.NewJSONHandler(os.Stdout, nil)))
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	serviceToken := os.Getenv("VOLUMEOS_SERVICE_TOKEN")
	if len(serviceToken) < 32 {
		slog.Error("VOLUMEOS_SERVICE_TOKEN must contain at least 32 bytes")
		os.Exit(1)
	}
	keycloak, err := NewKeycloakClientFromEnv()
	if err != nil {
		slog.Error("Keycloak configuration failed", "error", err)
		os.Exit(1)
	}
	postiz, err := NewPostizClientFromEnv()
	if err != nil {
		slog.Error("Postiz configuration failed", "error", err)
		os.Exit(1)
	}
	mailer := NewMailerFromEnv()
	if mailer == nil {
		slog.Warn("RESEND_API_KEY is not set: new sign-ins will be refused (their details are emailed)")
	}
	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}
	server := &http.Server{
		Addr: ":" + port, Handler: (&API{serviceToken: serviceToken, keycloak: keycloak, postiz: postiz, mailer: mailer}).routes(),
		ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 15 * time.Second,
		WriteTimeout: 45 * time.Second, IdleTimeout: 60 * time.Second,
	}
	go func() {
		<-ctx.Done()
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		_ = server.Shutdown(shutdownCtx)
	}()
	slog.Info("svc-provisioning listening", "address", server.Addr)
	if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		slog.Error("server failed", "error", err)
		os.Exit(1)
	}
}
