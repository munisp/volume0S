package main

// Offer and Bundle describe the supplied Q1 founding-rate book. They are
// commercial reference data, not proof that a price is currently active.
type Offer struct {
	ID               string      `json:"id"`
	Name             string      `json:"name"`
	Module           string      `json:"module"`
	CommercialStatus string      `json:"commercial_status"`
	Tiers            []OfferTier `json:"tiers"`
}

type OfferTier struct {
	ID              string         `json:"id"`
	SetupMinor      int64          `json:"setup_minor"`
	MonthlyMinor    int64          `json:"monthly_minor"`
	PackMinor       int64          `json:"pack_minor,omitempty"`
	Currency        string         `json:"currency"`
	TurnaroundHours int            `json:"turnaround_hours,omitempty"`
	Entitlements    map[string]int `json:"entitlements"`
}

type Bundle struct {
	ID               string   `json:"id"`
	SetupMinor       int64    `json:"setup_minor"`
	MonthlyMinor     int64    `json:"monthly_minor"`
	Currency         string   `json:"currency"`
	RequiredOffers   []string `json:"required_offers"`
	StudioPacksMonth int      `json:"studio_packs_per_month"`
}

var q1Offers = []Offer{
	{ID: "01", Name: "Come-Back Engine", Module: "campaigns.lifecycle", CommercialStatus: "operator-review-required", Tiers: []OfferTier{
		{ID: "starter", SetupMinor: 4000000, MonthlyMinor: 3500000, Currency: "NGN", Entitlements: map[string]int{"list_max": 999, "reactivation_touches": 3, "full_list_campaigns": 2}},
		{ID: "growth", SetupMinor: 7500000, MonthlyMinor: 8500000, Currency: "NGN", Entitlements: map[string]int{"list_max": 5000, "reactivation_touches": 3, "full_list_campaigns": 2}},
		{ID: "pro", SetupMinor: 15000000, MonthlyMinor: 15000000, Currency: "NGN", Entitlements: map[string]int{"list_max": 0, "reactivation_touches": 3, "full_list_campaigns": 2, "multi_branch": 1}},
	}},
	{ID: "02", Name: "Content Cadence", Module: "campaigns.social", CommercialStatus: "operator-review-required", Tiers: []OfferTier{
		{ID: "starter", SetupMinor: 2500000, MonthlyMinor: 3500000, Currency: "NGN", Entitlements: map[string]int{"posts": 8, "platforms": 1}},
		{ID: "growth", SetupMinor: 4000000, MonthlyMinor: 5500000, Currency: "NGN", Entitlements: map[string]int{"posts": 16, "platforms": 3, "performance_report": 1}},
		{ID: "pro", SetupMinor: 7500000, MonthlyMinor: 12000000, Currency: "NGN", Entitlements: map[string]int{"posts": 30, "platforms": 0, "reels": 2, "ad_creative_support": 1, "strategy_call": 1}},
	}},
	{ID: "03", Name: "Call Quality Scorecards", Module: "scorecards", CommercialStatus: "source-only-unpriced", Tiers: []OfferTier{}},
	{ID: "04", Name: "AI Front Desk", Module: "conversations", CommercialStatus: "source-only-unpriced", Tiers: []OfferTier{}},
	{ID: "05", Name: "Books on Autopilot", Module: "books", CommercialStatus: "source-only-unpriced", Tiers: []OfferTier{}},
	{ID: "06", Name: "Shopfront Studio", Module: "studio", CommercialStatus: "operator-review-required", Tiers: []OfferTier{
		{ID: "starter", PackMinor: 2000000, Currency: "NGN", TurnaroundHours: 48, Entitlements: map[string]int{"images": 5, "studio_white": 1, "lifestyle": 2}},
		{ID: "growth", PackMinor: 3000000, Currency: "NGN", TurnaroundHours: 48, Entitlements: map[string]int{"images": 7, "studio_white": 1, "lifestyle": 2, "infographic": 1, "before_after": 1}},
		{ID: "pro", PackMinor: 5500000, Currency: "NGN", TurnaroundHours: 48, Entitlements: map[string]int{"images": 12, "short_video_loops": 1}},
	}},
	{ID: "07", Name: "Compliance Watchtower", Module: "compliance", CommercialStatus: "source-only-unpriced", Tiers: []OfferTier{}},
	{ID: "08", Name: "Funding Readiness", Module: "funding", CommercialStatus: "source-only-unpriced", Tiers: []OfferTier{}},
}

var q1Bundles = []Bundle{{
	ID: "growth-cadence-studio", SetupMinor: 5000000, MonthlyMinor: 9900000,
	Currency: "NGN", RequiredOffers: []string{"02:growth", "06:growth"}, StudioPacksMonth: 2,
}}

func OfferCatalog() ([]Offer, []Bundle) {
	offers := make([]Offer, len(q1Offers))
	copy(offers, q1Offers)
	for i := range offers {
		offers[i].Tiers = append([]OfferTier(nil), q1Offers[i].Tiers...)
		for j := range offers[i].Tiers {
			offers[i].Tiers[j].Entitlements = make(map[string]int, len(q1Offers[i].Tiers[j].Entitlements))
			for key, value := range q1Offers[i].Tiers[j].Entitlements {
				offers[i].Tiers[j].Entitlements[key] = value
			}
		}
	}
	bundles := append([]Bundle(nil), q1Bundles...)
	for i := range bundles {
		bundles[i].RequiredOffers = append([]string(nil), q1Bundles[i].RequiredOffers...)
	}
	return offers, bundles
}
