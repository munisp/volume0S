package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
)

// fakeKeycloak is a tiny in-memory Keycloak admin API: users with attributes, realm roles and role mappings.
type fakeKeycloak struct {
	mu       sync.Mutex
	users    map[string]*kcUser
	mappings map[string][]kcRole
	roles    map[string]kcRole
	next     int
}

func newFakeKeycloak() *fakeKeycloak {
	f := &fakeKeycloak{users: map[string]*kcUser{}, mappings: map[string][]kcRole{}, roles: map[string]kcRole{}}
	for i, name := range append(slices2(personRoles), "offline_access") {
		f.roles[name] = kcRole{ID: "role-" + string(rune('a'+i)), Name: name}
	}
	return f
}

func slices2(in []string) []string { return append([]string{}, in...) }

func (f *fakeKeycloak) add(tenant, email, role string) string {
	f.next++
	id := "00000000-0000-4000-8000-0000000000" + string(rune('0'+f.next/10)) + string(rune('0'+f.next%10))
	f.users[id] = &kcUser{ID: id, Username: email, Email: email, Enabled: true, Attributes: map[string][]string{"tenant_id": {tenant}}}
	f.mappings[id] = []kcRole{f.roles[role], f.roles["offline_access"]}
	return id
}

func (f *fakeKeycloak) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	f.mu.Lock()
	defer f.mu.Unlock()
	path := strings.TrimPrefix(r.URL.Path, "/admin")
	switch {
	case r.URL.Path == "/token":
		_ = json.NewEncoder(w).Encode(map[string]any{"access_token": "t", "expires_in": 300})
	case path == "/users" && r.Method == http.MethodGet:
		out := []kcUser{}
		for _, u := range f.users {
			if q := r.URL.Query().Get("q"); q != "" && !strings.Contains("tenant_id:"+u.tenant(), q) {
				continue
			}
			if name := r.URL.Query().Get("username"); name != "" && u.Username != name {
				continue
			}
			out = append(out, *u)
		}
		_ = json.NewEncoder(w).Encode(out)
	case path == "/users" && r.Method == http.MethodPost:
		var u kcUser
		_ = json.NewDecoder(r.Body).Decode(&u)
		for _, existing := range f.users {
			if existing.Username == u.Username {
				w.WriteHeader(http.StatusConflict)
				return
			}
		}
		f.next++
		u.ID = "00000000-0000-4000-8000-0000000000" + string(rune('0'+f.next/10)) + string(rune('0'+f.next%10))
		f.users[u.ID] = &u
		w.WriteHeader(http.StatusCreated)
	case strings.HasSuffix(path, "/role-mappings/realm"):
		id := strings.Split(path, "/")[2]
		var body []kcRole
		_ = json.NewDecoder(r.Body).Decode(&body)
		switch r.Method {
		case http.MethodGet:
			_ = json.NewEncoder(w).Encode(f.mappings[id])
			return
		case http.MethodPost:
			f.mappings[id] = append(f.mappings[id], body...)
		case http.MethodDelete:
			keep := []kcRole{}
			for _, have := range f.mappings[id] {
				drop := false
				for _, d := range body {
					drop = drop || d.Name == have.Name
				}
				if !drop {
					keep = append(keep, have)
				}
			}
			f.mappings[id] = keep
		}
		w.WriteHeader(http.StatusNoContent)
	case strings.HasPrefix(path, "/roles/"):
		_ = json.NewEncoder(w).Encode(f.roles[strings.TrimPrefix(path, "/roles/")])
	case strings.HasPrefix(path, "/users/"):
		u := f.users[strings.TrimPrefix(path, "/users/")]
		if u == nil {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		switch r.Method {
		case http.MethodGet:
			_ = json.NewEncoder(w).Encode(u)
		case http.MethodPut:
			var patch map[string]any
			_ = json.NewDecoder(r.Body).Decode(&patch)
			if enabled, ok := patch["enabled"].(bool); ok {
				u.Enabled = enabled
			}
			w.WriteHeader(http.StatusNoContent)
		case http.MethodDelete:
			delete(f.users, u.ID)
			w.WriteHeader(http.StatusNoContent)
		}
	default:
		w.WriteHeader(http.StatusNotFound)
	}
}

func peopleAPI(t *testing.T) (http.Handler, *fakeKeycloak) {
	t.Helper()
	fake := newFakeKeycloak()
	server := httptest.NewServer(fake)
	t.Cleanup(server.Close)
	kc := &KeycloakClient{adminURL: server.URL + "/admin", tokenURL: server.URL + "/token", clientID: "c", clientSecret: "s", httpClient: server.Client()}
	return (&API{serviceToken: strings.Repeat("x", 32), keycloak: kc}).routes(), fake
}

func call(t *testing.T, h http.Handler, method, path, tenant, body string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(method, path, bytes.NewBufferString(body))
	req.Header.Set("Authorization", "Bearer "+strings.Repeat("x", 32))
	req.Header.Set("X-VolumeOS-Tenant", tenant)
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	return rec
}

func TestPeopleAreScopedToTheirBusiness(t *testing.T) {
	h, fake := peopleAPI(t)
	fake.add("acme-ltd", "ada@acme.test", "delivery")
	other := fake.add("acme-ltd-two", "bo@two.test", "sales") // a tenant id that contains the first as a prefix

	list := call(t, h, http.MethodGet, "/v1/people", "acme-ltd", "")
	var body struct{ People []Person }
	_ = json.Unmarshal(list.Body.Bytes(), &body)
	if list.Code != 200 || len(body.People) != 1 || body.People[0].Email != "ada@acme.test" || len(body.People[0].Roles) != 1 || body.People[0].Roles[0] != "delivery" {
		t.Fatalf("list: %d %s", list.Code, list.Body.String())
	}
	// Changing someone from another business is "not found", and nothing changes.
	if r := call(t, h, http.MethodPatch, "/v1/people/"+other, "acme-ltd", `{"enabled":false}`); r.Code != 404 {
		t.Fatalf("cross-business update: %d", r.Code)
	}
	if !fake.users[other].Enabled {
		t.Fatal("the other business's person was changed")
	}
}

func TestCreatePersonGivesOneRoleAndAOneTimePassword(t *testing.T) {
	h, fake := peopleAPI(t)
	r := call(t, h, http.MethodPost, "/v1/people", "acme-ltd", `{"email":"Ada@Acme.test","first_name":"Ada","last_name":"Obi","role":"client-owner"}`)
	if r.Code != 201 {
		t.Fatalf("create: %d %s", r.Code, r.Body.String())
	}
	var body struct {
		Person            Person
		TemporaryPassword string `json:"temporary_password"`
	}
	_ = json.Unmarshal(r.Body.Bytes(), &body)
	if body.Person.Email != "ada@acme.test" || len(body.TemporaryPassword) != 16 || len(body.Person.Roles) != 1 || body.Person.Roles[0] != "client-owner" {
		t.Fatalf("created: %s", r.Body.String())
	}
	u := fake.users[body.Person.ID]
	if u.tenant() != "acme-ltd" || len(u.RequiredActions) != 1 || u.RequiredActions[0] != "UPDATE_PASSWORD" {
		t.Fatalf("stored user: %+v", u)
	}
	if dup := call(t, h, http.MethodPost, "/v1/people", "acme-ltd", `{"email":"ada@acme.test","first_name":"A","last_name":"O","role":"sales"}`); dup.Code != 409 {
		t.Fatalf("duplicate: %d", dup.Code)
	}
	if bad := call(t, h, http.MethodPost, "/v1/people", "acme-ltd", `{"email":"x","first_name":"A","last_name":"O","role":"owner"}`); bad.Code != 400 {
		t.Fatalf("invalid: %d", bad.Code)
	}
}

func TestChangingRoleKeepsExactlyOneVolumeOSRoleAndOtherRealmRoles(t *testing.T) {
	h, fake := peopleAPI(t)
	id := fake.add("acme-ltd", "ada@acme.test", "delivery")
	r := call(t, h, http.MethodPatch, "/v1/people/"+id, "acme-ltd", `{"role":"finance","enabled":false}`)
	if r.Code != 200 {
		t.Fatalf("update: %d %s", r.Code, r.Body.String())
	}
	names := []string{}
	for _, m := range fake.mappings[id] {
		names = append(names, m.Name)
	}
	if strings.Join(names, ",") != "offline_access,finance" || fake.users[id].Enabled {
		t.Fatalf("after update: roles=%v enabled=%v", names, fake.users[id].Enabled)
	}
}

func TestOneTimePasswordsDiffer(t *testing.T) {
	a, _ := oneTimePassword()
	b, _ := oneTimePassword()
	if a == b || len(a) != 16 {
		t.Fatalf("passwords %q %q", a, b)
	}
}
