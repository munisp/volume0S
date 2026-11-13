package main

import (
	"encoding/json"
	"strings"
	"testing"
)

func offerTier(t *testing.T, offerID, tierID string) OfferTier {
	t.Helper()
	offers, _ := OfferCatalog()
	for _, offer := range offers {
		if offer.ID != offerID {
			continue
		}
		for _, tier := range offer.Tiers {
			if tier.ID == tierID {
				return tier
			}
		}
	}
	t.Fatalf("missing offer %s tier %s", offerID, tierID)
	return OfferTier{}
}

func TestQ1OfferPricesAndEntitlements(t *testing.T) {
	tests := []struct {
		offer, tier          string
		setup, monthly, pack int64
		entitlement          string
		want                 int
	}{
		{"01", "starter", 4000000, 3500000, 0, "list_max", 999},
		{"01", "growth", 7500000, 8500000, 0, "list_max", 5000},
		{"01", "pro", 15000000, 15000000, 0, "multi_branch", 1},
		{"02", "starter", 2500000, 3500000, 0, "posts", 8},
		{"02", "growth", 4000000, 5500000, 0, "posts", 16},
		{"02", "pro", 7500000, 12000000, 0, "reels", 2},
		{"06", "starter", 0, 0, 2000000, "images", 5},
		{"06", "growth", 0, 0, 3000000, "images", 7},
		{"06", "pro", 0, 0, 5500000, "short_video_loops", 1},
	}
	for _, test := range tests {
		tier := offerTier(t, test.offer, test.tier)
		if tier.SetupMinor != test.setup || tier.MonthlyMinor != test.monthly || tier.PackMinor != test.pack {
			t.Errorf("%s/%s wrong prices: %+v", test.offer, test.tier, tier)
		}
		if tier.Currency != "NGN" || tier.Entitlements[test.entitlement] != test.want {
			t.Errorf("%s/%s wrong commercial terms: %+v", test.offer, test.tier, tier)
		}
		if test.offer == "06" && tier.TurnaroundHours != 48 {
			t.Errorf("%s/%s turnaround must be 48 hours", test.offer, test.tier)
		}
	}
}

func TestLaunchBundleAndCatalogIsolation(t *testing.T) {
	offers, bundles := OfferCatalog()
	if len(bundles) != 1 || bundles[0].MonthlyMinor != 9900000 || bundles[0].SetupMinor != 5000000 || bundles[0].StudioPacksMonth != 2 {
		t.Fatalf("wrong launch bundle: %+v", bundles)
	}
	offers[0].Tiers[0].MonthlyMinor = 1
	offers[0].Tiers[0].Entitlements["posts"] = 1000
	fresh, _ := OfferCatalog()
	if fresh[0].Tiers[0].MonthlyMinor != 3500000 || fresh[0].Tiers[0].Entitlements["posts"] == 1000 {
		t.Fatal("catalog caller mutated the source price book")
	}
	for _, offer := range fresh {
		if offer.CommercialStatus != "operator-review-required" && offer.CommercialStatus != "source-only-unpriced" {
			t.Fatalf("unreviewed price book marked active: %+v", offer)
		}
		if offer.CommercialStatus == "source-only-unpriced" && len(offer.Tiers) != 0 {
			t.Fatalf("source-only offer must not invent pricing: %+v", offer)
		}
	}
}

func TestDevelopingMarketCatalogHasTenUnpricedRevenueModules(t *testing.T) {
	offers, _ := OfferCatalog()
	if len(offers) != 35 {
		t.Fatalf("catalog must contain the original eight, ten commerce offers, eight Volume II offers, eight Volume III offers and Creators Home, got %d", len(offers))
	}
	modules := map[string]string{}
	for _, offer := range offers {
		modules[offer.ID] = offer.Module
		if offer.ID >= "09" && offer.ID <= "18" && (offer.CommercialStatus != "source-only-unpriced" || len(offer.Tiers) != 0) {
			t.Fatalf("commerce offer must not invent commercial terms: %+v", offer)
		}
	}
	for id, module := range map[string]string{
		"09": "commerce.pos", "10": "commerce.inventory", "11": "commerce.storefront",
		"12": "commerce.quotes", "13": "commerce.instalments", "14": "commerce.bookings",
		"15": "commerce.loyalty", "16": "commerce.purchasing", "17": "commerce.support", "18": "commerce.fieldsales",
	} {
		if modules[id] != module {
			t.Errorf("offer %s has module %q, want %q", id, modules[id], module)
		}
	}
}

func TestVolumeIICatalogPricingAndModuleMapping(t *testing.T) {
	offers, _ := OfferCatalog()
	modules := map[string]string{}
	for _, offer := range offers {
		modules[offer.ID] = offer.Module
		if offer.ID >= "19" && offer.ID <= "26" && offer.CommercialStatus != "operator-review-required" {
			t.Fatalf("Volume II supplied prices must remain operator-review-required: %+v", offer)
		}
	}
	for id, module := range map[string]string{
		"19": "media.dubbing", "20": "security.audit", "21": "knowledge.copilot", "22": "leads.intent",
		"23": "analytics.dashboard", "24": "documents.archive", "25": "collections.receivables", "26": "marketing.attribution",
	} {
		if modules[id] != module {
			t.Errorf("Volume II offer %s has module %q, want %q", id, modules[id], module)
		}
	}
	tests := []struct {
		offer, tier          string
		setup, monthly, pack int64
		entitlement          string
		want                 int
	}{
		{"19", "starter", 4000000, 4500000, 0, "videos_per_month", 2},
		{"19", "growth", 6000000, 7500000, 0, "languages", 2},
		{"19", "pro", 10000000, 12000000, 0, "series_handling", 1},
		{"20", "audit", 0, 0, 22000000, "evidence_pack", 1},
		{"20", "re-audit", 0, 8500000, 0, "release_re_audit", 1},
		{"21", "department", 12000000, 6500000, 0, "monday_pipeline_review", 1},
		{"22", "daily-digest", 3000000, 5500000, 0, "opportunities_per_day", 5},
		{"23", "monday", 12000000, 7000000, 0, "kpi_metrics", 10},
		{"24", "archive", 10000000, 5000000, 0, "searchable_archive", 1},
		{"25", "collections", 5000000, 5500000, 0, "reminder_steps", 4},
		{"26", "attribution", 3000000, 4000000, 0, "monthly_attribution_report", 1},
	}
	for _, test := range tests {
		tier := offerTier(t, test.offer, test.tier)
		if tier.SetupMinor != test.setup || tier.MonthlyMinor != test.monthly || tier.PackMinor != test.pack {
			t.Errorf("%s/%s wrong Volume II prices: %+v", test.offer, test.tier, tier)
		}
		if tier.Currency != "NGN" || tier.Entitlements[test.entitlement] != test.want {
			t.Errorf("%s/%s wrong Volume II terms: %+v", test.offer, test.tier, tier)
		}
	}
	if err := ValidateSubscription("19", "starter"); err == nil {
		t.Fatal("catalog-only Volume II offer must not become subscribable before its delivery workflow exists")
	}
}

func TestVolumeIIIVideoCatalogIsUnpricedAndNotSubscribable(t *testing.T) {
	offers, _ := OfferCatalog()
	want := map[string]struct {
		name   string
		module string
	}{
		"27": {"Website Makeover Studio", "web.makeover"},
		"28": {"Video Ad Packs", "media.ads"},
		"29": {"Course Tutor", "education.tutor"},
		"30": {"AI Team Install", "agents.teams"},
		"31": {"Replacement Part Design", "cad.parts"},
		"32": {"Industry Audio Briefings", "research.briefings"},
		"33": {"Bid Radar", "procurement.radar"},
		"34": {"Website Guard", "monitoring.website"},
	}
	seen := map[string]bool{}
	for _, offer := range offers {
		expected, ok := want[offer.ID]
		if !ok {
			continue
		}
		seen[offer.ID] = true
		if offer.Name != expected.name || offer.Module != expected.module {
			t.Errorf("Volume III offer %s has %q/%q, want %q/%q", offer.ID, offer.Name, offer.Module, expected.name, expected.module)
		}
		if offer.CommercialStatus != "source-only-unpriced" || len(offer.Tiers) != 0 {
			t.Errorf("Volume III offer must not invent prices or entitlements: %+v", offer)
		}
		if err := ValidateSubscription(offer.ID, "starter"); err == nil {
			t.Errorf("catalog-only Volume III offer %s became subscribable before its delivery workflow exists", offer.ID)
		}
	}
	if len(seen) != len(want) {
		t.Fatalf("Volume III catalog is incomplete: got %d of %d offers", len(seen), len(want))
	}
}

func TestRemainingOffersArePresentWithoutInventedPrices(t *testing.T) {
	offers, _ := OfferCatalog()
	seen := map[string]bool{}
	for _, offer := range offers {
		if offer.ID == "03" || offer.ID == "04" || offer.ID == "05" || offer.ID == "07" || offer.ID == "08" {
			seen[offer.ID] = true
			if offer.CommercialStatus != "source-only-unpriced" || len(offer.Tiers) != 0 {
				t.Fatalf("remaining offer has an unsupported commercial term: %+v", offer)
			}
		}
	}
	if len(seen) != 5 {
		t.Fatalf("remaining offers missing from catalog: %+v", seen)
	}
}

func TestUnpricedOffersEncodeEmptyTierLists(t *testing.T) {
	offers, bundles := OfferCatalog()
	raw, err := json.Marshal(map[string]any{"offers": offers, "bundles": bundles})
	if err != nil {
		t.Fatal(err)
	}
	// Clients index tiers[0]; a null list crashes them.
	if strings.Contains(string(raw), `"tiers":null`) || strings.Contains(string(raw), `"required_offers":null`) {
		t.Fatalf("catalog encodes a null list: %s", raw)
	}
	if !strings.Contains(string(raw), `"tiers":[]`) {
		t.Fatal("expected unpriced offers to encode tiers as []")
	}
}

func TestCreatorsHomeIsListedButNotSubscribableUntilPriced(t *testing.T) {
	offers, _ := OfferCatalog()
	for _, offer := range offers {
		if offer.ID != "35" {
			continue
		}
		if offer.Name != "Creators Home" || offer.Module != "media.creators" || offer.CommercialStatus != "source-only-unpriced" || len(offer.Tiers) != 0 {
			t.Fatalf("Creators Home must be listed unpriced: %+v", offer)
		}
		if err := ValidateSubscription("35", "starter"); err == nil {
			t.Fatal("Creators Home became subscribable before it has prices")
		}
		return
	}
	t.Fatal("Creators Home (35) is missing from the catalogue")
}
