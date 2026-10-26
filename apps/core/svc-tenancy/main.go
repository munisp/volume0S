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
	logger := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	slog.SetDefault(logger)
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	auth, err := NewAuthenticator()
	if err != nil {
		slog.Error("authentication configuration failed", "error", err)
		os.Exit(1)
	}
	var store Store
	if os.Getenv("STORE") == "memory" {
		if os.Getenv("VO_ENV") == "prod" {
			slog.Error("memory store cannot be used in prod")
			os.Exit(1)
		}
		store = NewMemoryStore()
	} else {
		postgres, err := NewPostgresStore(ctx, os.Getenv("DATABASE_URL"))
		if err != nil {
			slog.Error("postgres initialization failed", "error", err)
			os.Exit(1)
		}
		defer postgres.Close()
		store = postgres
	}

	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}
	server := &http.Server{
		Addr: ":" + port, Handler: (&API{store: store, auth: auth}).routes(),
		ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 15 * time.Second,
		WriteTimeout: 30 * time.Second, IdleTimeout: 60 * time.Second,
	}
	go func() {
		<-ctx.Done()
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		_ = server.Shutdown(shutdownCtx)
	}()
	slog.Info("svc-tenancy listening", "address", server.Addr)
	if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		slog.Error("server failed", "error", err)
		os.Exit(1)
	}
}
