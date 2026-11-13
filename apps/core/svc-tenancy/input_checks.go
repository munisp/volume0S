package main

import (
	"fmt"
	"regexp"
	"strings"
	"unicode/utf8"
)

// Checks added after the 2026-11-09 black-box pass. They only refuse input; they never change how a recipient is
// normalised, because a suppression must keep matching the digest the dispatch path computes for the same address.

// Control characters (except tab and line breaks), DEL, C1 controls and bidirectional overrides.
var hiddenCharacters = regexp.MustCompile(`[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\x{80}-\x{9f}\x{202a}-\x{202e}\x{2066}-\x{2069}]`)

var (
	emailRecipient = regexp.MustCompile(`^[^\s@]+@[^\s@]+\.[^\s@]+$`)
	// Same convention as billing WhatsApp numbers elsewhere in VolumeOS: + then 8 to 15 digits (E.164).
	phoneRecipient = regexp.MustCompile(`^\+[1-9][0-9]{7,14}$`)
)

func invalid(format string, args ...any) error {
	return fmt.Errorf("%w: %s", ErrInvalidChange, fmt.Sprintf(format, args...))
}

func checkRecipient(channel, recipient string) error {
	value := strings.TrimSpace(recipient)
	if hiddenCharacters.MatchString(value) {
		return invalid("the recipient contains hidden characters")
	}
	switch channel {
	case "email":
		if len(value) > 320 || !emailRecipient.MatchString(value) {
			return invalid("for email, the recipient must be an email address")
		}
	case "sms", "whatsapp":
		if !phoneRecipient.MatchString(value) {
			return invalid("for %s, the recipient must be an international number such as +2348012345678", channel)
		}
	case "social":
		if value == "" || utf8.RuneCountInString(value) > 256 || strings.ContainsAny(value, " \t\r\n") {
			return invalid("for social, the recipient must be a handle or account id without spaces")
		}
	default:
		return invalid("the channel must be email, sms, whatsapp or social")
	}
	return nil
}

func checkSuppressionInput(channel, recipient, reason string, suppressed bool) error {
	if err := checkRecipient(channel, recipient); err != nil {
		return err
	}
	reason = strings.TrimSpace(reason)
	switch {
	case hiddenCharacters.MatchString(reason):
		return invalid("the reason contains hidden characters")
	case len(reason) > 500:
		return invalid("the reason must be 500 characters or fewer")
	case suppressed && reason == "":
		return invalid("a reason is required to suppress a recipient")
	}
	return nil
}

// checkName validates a display name (tenant or workspace): 2 to 120 characters, no hidden characters.
func checkName(what, name string) error {
	value := strings.TrimSpace(name)
	if hiddenCharacters.MatchString(value) {
		return invalid("the %s contains hidden characters", what)
	}
	if length := utf8.RuneCountInString(value); length < 2 || length > 120 {
		return invalid("the %s must be 2 to 120 characters", what)
	}
	return nil
}
