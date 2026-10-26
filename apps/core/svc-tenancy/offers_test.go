package main

import "testing"

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
		if offer.CommercialStatus != "operator-review-required" {
			t.Fatalf("unreviewed price book marked active: %+v", offer)
		}
	}
}
