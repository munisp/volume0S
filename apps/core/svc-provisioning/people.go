package main

// People: the sign-ins of one business, kept in Keycloak (realm volumeos). Each person carries the business in the
// tenant_id user attribute (the token's tenant_id claim comes from it) and exactly one VolumeOS realm role.
//
// Every operation is scoped to the business in X-VolumeOS-Tenant: a person whose tenant_id differs is "not found",
// so a request for one business can never read or change another's people. Who may call these (super operators;
// only platform operators grant super-operator) is decided by svc-orchestrator, the only caller.

import (
	"context"
	"crypto/rand"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"math/big"
	"net/http"
	"net/url"
	"regexp"
	"slices"
	"strings"
)

var (
	personRoles   = []string{"super-operator", "delivery", "sales", "analyst", "finance", "auditor", "partner", "client-owner"}
	personIDRe    = regexp.MustCompile(`^[a-f0-9-]{36}$`)
	emailRe       = regexp.MustCompile(`^[^@\s]+@[^@\s]+\.[^@\s]+$`)
	ErrPersonGone = errors.New("person not found in this business")
	ErrPersonDup  = errors.New("someone with this email already has a sign-in")
	// ErrAlreadySignedIn: the person chose their own password, so there's no one-time password to send again.
	ErrAlreadySignedIn = errors.New("they've already signed in and chosen their own password")
	ErrMailFailed      = errors.New("the sign-in email couldn't be sent")
)

type Person struct {
	ID        string   `json:"id"`
	Username  string   `json:"username"`
	Email     string   `json:"email"`
	FirstName string   `json:"first_name"`
	LastName  string   `json:"last_name"`
	Enabled   bool     `json:"enabled"`
	Roles     []string `json:"roles"`
	CreatedAt int64    `json:"created_at"`
	// MustSetPassword: the person hasn't replaced their one-time password yet.
	MustSetPassword bool `json:"must_set_password"`
}

type kcUser struct {
	ID               string              `json:"id"`
	Username         string              `json:"username"`
	Email            string              `json:"email"`
	FirstName        string              `json:"firstName"`
	LastName         string              `json:"lastName"`
	Enabled          bool                `json:"enabled"`
	CreatedTimestamp int64               `json:"createdTimestamp"`
	Attributes       map[string][]string `json:"attributes"`
	RequiredActions  []string            `json:"requiredActions"`
}

type kcRole struct {
	ID   string `json:"id"`
	Name string `json:"name"`
}

func (u kcUser) tenant() string {
	if values := u.Attributes["tenant_id"]; len(values) > 0 {
		return values[0]
	}
	return ""
}

func (k *KeycloakClient) personRoles(ctx context.Context, id string) ([]kcRole, error) {
	content, status, err := k.request(ctx, http.MethodGet, "/users/"+url.PathEscape(id)+"/role-mappings/realm", nil, nil)
	if err != nil {
		return nil, err
	}
	if status != http.StatusOK {
		return nil, fmt.Errorf("Keycloak role lookup returned %d", status)
	}
	var roles []kcRole
	if err := json.Unmarshal(content, &roles); err != nil {
		return nil, errors.New("Keycloak role lookup returned invalid JSON")
	}
	return slices.DeleteFunc(roles, func(r kcRole) bool { return !slices.Contains(personRoles, r.Name) }), nil
}

func (k *KeycloakClient) toPerson(ctx context.Context, u kcUser) (Person, error) {
	roles, err := k.personRoles(ctx, u.ID)
	if err != nil {
		return Person{}, err
	}
	names := []string{}
	for _, r := range roles {
		names = append(names, r.Name)
	}
	return Person{ID: u.ID, Username: u.Username, Email: u.Email, FirstName: u.FirstName, LastName: u.LastName,
		Enabled: u.Enabled, Roles: names, CreatedAt: u.CreatedTimestamp,
		MustSetPassword: slices.Contains(u.RequiredActions, "UPDATE_PASSWORD")}, nil
}

// user loads one person, refusing (as not found) anyone outside the business.
func (k *KeycloakClient) user(ctx context.Context, tenantID, id string) (kcUser, error) {
	if !personIDRe.MatchString(id) {
		return kcUser{}, ErrPersonGone
	}
	content, status, err := k.request(ctx, http.MethodGet, "/users/"+url.PathEscape(id), nil, nil)
	if err != nil {
		return kcUser{}, err
	}
	if status == http.StatusNotFound {
		return kcUser{}, ErrPersonGone
	}
	if status != http.StatusOK {
		return kcUser{}, fmt.Errorf("Keycloak user lookup returned %d", status)
	}
	var u kcUser
	if err := json.Unmarshal(content, &u); err != nil {
		return kcUser{}, errors.New("Keycloak user lookup returned invalid JSON")
	}
	if u.tenant() != tenantID {
		return kcUser{}, ErrPersonGone
	}
	return u, nil
}

func (k *KeycloakClient) ListPeople(ctx context.Context, tenantID string) ([]Person, error) {
	query := url.Values{"q": {"tenant_id:" + tenantID}, "briefRepresentation": {"false"}, "max": {"500"}}
	content, status, err := k.request(ctx, http.MethodGet, "/users", query, nil)
	if err != nil {
		return nil, err
	}
	if status != http.StatusOK {
		return nil, fmt.Errorf("Keycloak user search returned %d", status)
	}
	var users []kcUser
	if err := json.Unmarshal(content, &users); err != nil {
		return nil, errors.New("Keycloak user search returned invalid JSON")
	}
	people := []Person{}
	for _, u := range users {
		if u.tenant() != tenantID { // the search matches substrings; keep exact matches only
			continue
		}
		p, err := k.toPerson(ctx, u)
		if err != nil {
			return nil, err
		}
		people = append(people, p)
	}
	return people, nil
}

// CreatePerson makes the sign-in with a one-time password the person must replace at first sign-in.
func (k *KeycloakClient) CreatePerson(ctx context.Context, tenantID, email, first, last, role string) (Person, string, error) {
	email = strings.ToLower(strings.TrimSpace(email))
	password, err := oneTimePassword()
	if err != nil {
		return Person{}, "", err
	}
	payload := map[string]any{
		"username": email, "email": email, "firstName": strings.TrimSpace(first), "lastName": strings.TrimSpace(last),
		"enabled": true, "emailVerified": true, "attributes": map[string][]string{"tenant_id": {tenantID}},
		"requiredActions": []string{"UPDATE_PASSWORD"},
		"credentials":     []map[string]any{{"type": "password", "value": password, "temporary": true}},
	}
	_, status, err := k.request(ctx, http.MethodPost, "/users", nil, payload)
	if err != nil {
		return Person{}, "", err
	}
	if status == http.StatusConflict {
		return Person{}, "", ErrPersonDup
	}
	if status != http.StatusCreated {
		return Person{}, "", fmt.Errorf("Keycloak user creation returned %d", status)
	}
	content, status, err := k.request(ctx, http.MethodGet, "/users", url.Values{"username": {email}, "exact": {"true"}, "briefRepresentation": {"false"}}, nil)
	var found []kcUser
	if err != nil || status != http.StatusOK || json.Unmarshal(content, &found) != nil || len(found) != 1 || found[0].tenant() != tenantID {
		return Person{}, "", errors.New("Keycloak user creation could not be confirmed")
	}
	if err := k.setRole(ctx, found[0].ID, role); err != nil {
		// Don't leave a sign-in without a role behind.
		_, _, _ = k.request(ctx, http.MethodDelete, "/users/"+url.PathEscape(found[0].ID), nil, nil)
		return Person{}, "", err
	}
	person, err := k.toPerson(ctx, found[0])
	return person, password, err
}

// ResetOneTimePassword gives someone who hasn't signed in yet a fresh one-time password (for a lost sign-in email).
// Someone who has already chosen their own password is refused: this must never replace a password in use.
func (k *KeycloakClient) ResetOneTimePassword(ctx context.Context, tenantID, id string) (Person, string, error) {
	u, err := k.user(ctx, tenantID, id)
	if err != nil {
		return Person{}, "", err
	}
	if !slices.Contains(u.RequiredActions, "UPDATE_PASSWORD") {
		return Person{}, "", ErrAlreadySignedIn
	}
	password, err := oneTimePassword()
	if err != nil {
		return Person{}, "", err
	}
	_, status, err := k.request(ctx, http.MethodPut, "/users/"+url.PathEscape(id)+"/reset-password", nil,
		map[string]any{"type": "password", "value": password, "temporary": true})
	if err != nil {
		return Person{}, "", err
	}
	if status != http.StatusNoContent {
		return Person{}, "", fmt.Errorf("Keycloak password reset returned %d", status)
	}
	person, err := k.toPerson(ctx, u)
	return person, password, err
}

// UpdatePerson changes the role and/or turns the sign-in on or off.
func (k *KeycloakClient) UpdatePerson(ctx context.Context, tenantID, id string, role *string, enabled *bool) (Person, error) {
	u, err := k.user(ctx, tenantID, id)
	if err != nil {
		return Person{}, err
	}
	if enabled != nil {
		_, status, err := k.request(ctx, http.MethodPut, "/users/"+url.PathEscape(id), nil, map[string]any{"enabled": *enabled})
		if err != nil {
			return Person{}, err
		}
		if status != http.StatusNoContent {
			return Person{}, fmt.Errorf("Keycloak user update returned %d", status)
		}
		u.Enabled = *enabled
	}
	if role != nil {
		if err := k.setRole(ctx, id, *role); err != nil {
			return Person{}, err
		}
	}
	return k.toPerson(ctx, u)
}

// setRole leaves the person with exactly this one VolumeOS role (other realm roles are untouched).
func (k *KeycloakClient) setRole(ctx context.Context, id, role string) error {
	if !slices.Contains(personRoles, role) {
		return fmt.Errorf("unknown role %q", role)
	}
	current, err := k.personRoles(ctx, id)
	if err != nil {
		return err
	}
	path := "/users/" + url.PathEscape(id) + "/role-mappings/realm"
	if drop := slices.DeleteFunc(slices.Clone(current), func(r kcRole) bool { return r.Name == role }); len(drop) > 0 {
		if _, status, err := k.request(ctx, http.MethodDelete, path, nil, drop); err != nil || status != http.StatusNoContent {
			return fmt.Errorf("Keycloak role removal failed (%d): %v", status, err)
		}
	}
	if slices.ContainsFunc(current, func(r kcRole) bool { return r.Name == role }) {
		return nil
	}
	content, status, err := k.request(ctx, http.MethodGet, "/roles/"+url.PathEscape(role), nil, nil)
	var wanted kcRole
	if err != nil || status != http.StatusOK || json.Unmarshal(content, &wanted) != nil || wanted.ID == "" {
		return fmt.Errorf("Keycloak role %q lookup failed (%d)", role, status)
	}
	if _, status, err := k.request(ctx, http.MethodPost, path, nil, []kcRole{wanted}); err != nil || status != http.StatusNoContent {
		return fmt.Errorf("Keycloak role grant failed (%d): %v", status, err)
	}
	return nil
}

// oneTimePassword: 16 characters from an unambiguous alphabet, with a digit, an upper, a lower and a symbol so it
// passes typical realm password policies. The person replaces it at first sign-in.
func oneTimePassword() (string, error) {
	sets := []string{"ABCDEFGHJKLMNPQRSTUVWXYZ", "abcdefghijkmnpqrstuvwxyz", "23456789", "!#%+=?"}
	all := strings.Join(sets, "")
	out := make([]byte, 0, 16)
	for i := 0; i < 16; i++ {
		alphabet := all
		if i < len(sets) {
			alphabet = sets[i]
		}
		n, err := rand.Int(rand.Reader, big.NewInt(int64(len(alphabet))))
		if err != nil {
			return "", err
		}
		out = append(out, alphabet[n.Int64()])
	}
	// Shuffle so the guaranteed characters aren't always first.
	for i := len(out) - 1; i > 0; i-- {
		j, err := rand.Int(rand.Reader, big.NewInt(int64(i+1)))
		if err != nil {
			return "", err
		}
		out[i], out[j.Int64()] = out[j.Int64()], out[i]
	}
	return string(out), nil
}

// HTTP handlers (service token + X-VolumeOS-Tenant, see API.authenticate).

func (a *API) listPeople(w http.ResponseWriter, r *http.Request) {
	people, err := a.keycloak.ListPeople(r.Context(), r.Header.Get("X-VolumeOS-Tenant"))
	if err != nil {
		peopleError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"people": people})
}

func (a *API) createPerson(w http.ResponseWriter, r *http.Request) {
	var input struct {
		Email        string `json:"email"`
		FirstName    string `json:"first_name"`
		LastName     string `json:"last_name"`
		Role         string `json:"role"`
		BusinessName string `json:"business_name"`
	}
	if !decode(w, r, &input) {
		return
	}
	if !emailRe.MatchString(strings.TrimSpace(input.Email)) || strings.TrimSpace(input.FirstName) == "" ||
		strings.TrimSpace(input.LastName) == "" || !slices.Contains(personRoles, input.Role) {
		writeError(w, http.StatusBadRequest, "invalid_request", "an email, first name, last name and a known role are required")
		return
	}
	testAddress := isTestAddress(strings.TrimSpace(input.Email))
	if a.mailer == nil && !testAddress {
		peopleError(w, ErrMailNotConfigured)
		return
	}
	tenant := r.Header.Get("X-VolumeOS-Tenant")
	person, password, err := a.keycloak.CreatePerson(r.Context(), tenant, input.Email, input.FirstName, input.LastName, input.Role)
	if err != nil {
		peopleError(w, err)
		return
	}
	a.deliverSignIn(w, r, person, password, input.BusinessName, testAddress, http.StatusCreated, func() {
		// Nobody could reach a sign-in whose details never arrived: remove it so it can simply be added again.
		_, _, _ = a.keycloak.request(r.Context(), http.MethodDelete, "/users/"+url.PathEscape(person.ID), nil, nil)
	})
}

// deliverSignIn emails the one-time password to the person (never to the operator). A test address isn't
// emailed; its password comes back as test_password so test runs can sign in.
func (a *API) deliverSignIn(w http.ResponseWriter, r *http.Request, person Person, password, business string, testAddress bool,
	status int, undo func()) {
	if testAddress {
		writeJSON(w, status, map[string]any{"person": person, "sign_in_email": "not_sent_test_address", "test_password": password})
		return
	}
	if err := a.mailer.SendSignIn(r.Context(), SignInEmail{To: person.Email, FirstName: person.FirstName, Business: business, Password: password}); err != nil {
		slog.Error("sign-in email failed", "person", person.ID, "error", err)
		if undo == nil {
			writeError(w, http.StatusServiceUnavailable, "sign_in_email_failed", "the sign-in email couldn't be sent; try again")
			return
		}
		undo()
		peopleError(w, ErrMailFailed)
		return
	}
	writeJSON(w, status, map[string]any{"person": person, "sign_in_email": "sent"})
}

func (a *API) updatePerson(w http.ResponseWriter, r *http.Request) {
	var input struct {
		Role         *string `json:"role"`
		Enabled      *bool   `json:"enabled"`
		SendSignIn   bool    `json:"send_sign_in"`
		BusinessName string  `json:"business_name"`
	}
	if !decode(w, r, &input) {
		return
	}
	tenant, id := r.Header.Get("X-VolumeOS-Tenant"), r.PathValue("person")
	if input.SendSignIn {
		if input.Role != nil || input.Enabled != nil {
			writeError(w, http.StatusBadRequest, "invalid_request", "send the sign-in email on its own, not with other changes")
			return
		}
		current, err := a.keycloak.user(r.Context(), tenant, id)
		if err != nil {
			peopleError(w, err)
			return
		}
		testAddress := isTestAddress(current.Email)
		if a.mailer == nil && !testAddress {
			peopleError(w, ErrMailNotConfigured)
			return
		}
		person, password, err := a.keycloak.ResetOneTimePassword(r.Context(), tenant, id)
		if err != nil {
			peopleError(w, err)
			return
		}
		a.deliverSignIn(w, r, person, password, input.BusinessName, testAddress, http.StatusOK, nil)
		return
	}
	if (input.Role == nil && input.Enabled == nil) || (input.Role != nil && !slices.Contains(personRoles, *input.Role)) {
		writeError(w, http.StatusBadRequest, "invalid_request", "a known role and/or enabled is required")
		return
	}
	person, err := a.keycloak.UpdatePerson(r.Context(), tenant, id, input.Role, input.Enabled)
	if err != nil {
		peopleError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"person": person})
}

func peopleError(w http.ResponseWriter, err error) {
	switch {
	case errors.Is(err, ErrPersonGone):
		writeError(w, http.StatusNotFound, "not_found", "that person isn't in this business")
	case errors.Is(err, ErrPersonDup):
		writeError(w, http.StatusConflict, "person_exists", err.Error())
	case errors.Is(err, ErrAlreadySignedIn):
		writeError(w, http.StatusConflict, "already_signed_in", err.Error())
	case errors.Is(err, ErrMailNotConfigured):
		writeError(w, http.StatusServiceUnavailable, "email_not_configured", err.Error())
	case errors.Is(err, ErrMailFailed):
		writeError(w, http.StatusServiceUnavailable, "sign_in_email_failed", "the sign-in email couldn't be sent, so the sign-in wasn't made; try again")
	default:
		writeError(w, http.StatusBadGateway, "identity_unavailable", "Keycloak could not complete the change")
	}
}
