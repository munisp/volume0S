package main

// Sign-in emails: a new person's address, one-time password and the console link go to the person themselves,
// through Resend. The password is never returned to the operator who made the sign-in, and never stored here.
//
// Addresses at EMAIL_TEST_DOMAINS (default ".test,example.com") are never emailed: test runs on dev create such
// sign-ins and read the one-time password from the API answer instead (field test_password, only for them).

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"html"
	"io"
	"net/http"
	"os"
	"strings"
	"time"
	"unicode"
)

var ErrMailNotConfigured = errors.New("sign-in emails aren't set up on this deployment")

type SignInEmail struct {
	To, FirstName, Business, Password string
}

type Mailer interface {
	SendSignIn(ctx context.Context, m SignInEmail) error
}

type resendMailer struct {
	apiKey, from, consoleURL, endpoint string
	client                             *http.Client
}

// NewMailerFromEnv returns nil when RESEND_API_KEY is unset: creating people then fails with a clear message
// instead of making sign-ins nobody can reach.
func NewMailerFromEnv() Mailer {
	key := strings.TrimSpace(os.Getenv("RESEND_API_KEY"))
	if key == "" {
		return nil
	}
	from := os.Getenv("MAIL_FROM")
	if from == "" {
		from = "VolumeOS <volumeos@upi.dev>"
	}
	console := strings.TrimRight(os.Getenv("CONSOLE_URL"), "/")
	if console == "" {
		console = "https://volumeos.newfire.app"
	}
	return &resendMailer{apiKey: key, from: from, consoleURL: console, endpoint: "https://api.resend.com/emails",
		client: &http.Client{Timeout: 20 * time.Second}}
}

func testDomains() []string {
	raw := os.Getenv("EMAIL_TEST_DOMAINS")
	if raw == "" {
		raw = ".test,example.com"
	}
	out := []string{}
	for _, d := range strings.Split(raw, ",") {
		if d = strings.ToLower(strings.TrimSpace(d)); d != "" {
			out = append(out, d)
		}
	}
	return out
}

// isTestAddress: the address's domain is one of the test domains, or ends in one that starts with a dot (".test").
func isTestAddress(email string) bool {
	at := strings.LastIndex(email, "@")
	if at < 0 {
		return false
	}
	domain := strings.ToLower(email[at+1:])
	for _, d := range testDomains() {
		if domain == strings.TrimPrefix(d, ".") || (strings.HasPrefix(d, ".") && strings.HasSuffix(domain, d)) {
			return true
		}
	}
	return false
}

// cleanBusinessName keeps a business name safe to put in an email: printable, one line, no links, at most 80
// characters. Anything else is dropped and the email just says "your business".
func cleanBusinessName(name string) string {
	name = strings.TrimSpace(name)
	if name == "" || len([]rune(name)) > 80 || strings.Contains(strings.ToLower(name), "http") || strings.Contains(name, "://") || strings.Contains(name, "www.") {
		return ""
	}
	for _, r := range name {
		if !unicode.IsPrint(r) || unicode.Is(unicode.Bidi_Control, r) {
			return ""
		}
	}
	return name
}

func (m *resendMailer) SendSignIn(ctx context.Context, e SignInEmail) error {
	business := cleanBusinessName(e.Business)
	if business == "" {
		business = "your business"
	}
	greeting := "Hello"
	if first := strings.TrimSpace(e.FirstName); first != "" {
		greeting = "Hello " + first
	}
	text := fmt.Sprintf("%s,\n\nYou now have a VolumeOS sign-in for %s.\n\nSign in: %s\nEmail: %s\nOne-time password: %s\n\n"+
		"You'll choose your own password the first time you sign in. If you weren't expecting this email, you can ignore it.\n\n— VolumeOS\n",
		greeting, business, m.consoleURL, e.To, e.Password)
	h := html.EscapeString
	body := fmt.Sprintf(`<div style="font-family:system-ui,-apple-system,Segoe UI,sans-serif;font-size:15px;line-height:1.5;color:#1a1a1a;max-width:520px">
<p>%s,</p>
<p>You now have a VolumeOS sign-in for <strong>%s</strong>.</p>
<table style="border-collapse:collapse;margin:16px 0">
<tr><td style="padding:4px 16px 4px 0;color:#555">Email</td><td style="padding:4px 0;font-family:ui-monospace,Menlo,monospace">%s</td></tr>
<tr><td style="padding:4px 16px 4px 0;color:#555">One-time password</td><td style="padding:4px 0;font-family:ui-monospace,Menlo,monospace">%s</td></tr>
</table>
<p><a href="%s" style="display:inline-block;background:#1a1a1a;color:#fff;padding:10px 18px;border-radius:6px;text-decoration:none">Sign in to VolumeOS</a></p>
<p style="color:#555">You'll choose your own password the first time you sign in. If you weren't expecting this email, you can ignore it.</p>
</div>`, h(greeting), h(business), h(e.To), h(e.Password), h(m.consoleURL))
	payload, _ := json.Marshal(map[string]any{
		"from": m.from, "to": []string{e.To}, "subject": "Your VolumeOS sign-in", "text": text, "html": body,
	})
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, m.endpoint, bytes.NewReader(payload))
	if err != nil {
		return err
	}
	req.Header.Set("Authorization", "Bearer "+m.apiKey)
	req.Header.Set("Content-Type", "application/json")
	res, err := m.client.Do(req)
	if err != nil {
		return fmt.Errorf("Resend unreachable: %w", err)
	}
	defer res.Body.Close()
	if res.StatusCode/100 != 2 {
		detail, _ := io.ReadAll(io.LimitReader(res.Body, 300))
		return fmt.Errorf("Resend returned %d: %s", res.StatusCode, strings.TrimSpace(string(detail)))
	}
	return nil
}
