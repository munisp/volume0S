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
	{ID: "09", Name: "Offline POS & Cash Control", Module: "commerce.pos", CommercialStatus: "source-only-unpriced", Tiers: []OfferTier{}},
	{ID: "10", Name: "Inventory & Reorder Autopilot", Module: "commerce.inventory", CommercialStatus: "source-only-unpriced", Tiers: []OfferTier{}},
	{ID: "11", Name: "Social Storefront & Order Capture", Module: "commerce.storefront", CommercialStatus: "source-only-unpriced", Tiers: []OfferTier{}},
	{ID: "12", Name: "Quotes & Sales Pipeline", Module: "commerce.quotes", CommercialStatus: "source-only-unpriced", Tiers: []OfferTier{}},
	{ID: "13", Name: "Deposits & Instalment Orders", Module: "commerce.instalments", CommercialStatus: "source-only-unpriced", Tiers: []OfferTier{}},
	{ID: "14", Name: "Bookings & Service Dispatch", Module: "commerce.bookings", CommercialStatus: "source-only-unpriced", Tiers: []OfferTier{}},
	{ID: "15", Name: "Loyalty & Referral Rewards", Module: "commerce.loyalty", CommercialStatus: "source-only-unpriced", Tiers: []OfferTier{}},
	{ID: "16", Name: "Purchasing & Landed Cost", Module: "commerce.purchasing", CommercialStatus: "source-only-unpriced", Tiers: []OfferTier{}},
	{ID: "17", Name: "Omnichannel Support & SLA", Module: "commerce.support", CommercialStatus: "source-only-unpriced", Tiers: []OfferTier{}},
	{ID: "18", Name: "Field Sales & Van Stock", Module: "commerce.fieldsales", CommercialStatus: "source-only-unpriced", Tiers: []OfferTier{}},
	// Volume II opportunities 09–16 become catalog Offers 19–26 because the
	// developing-market commerce increment already occupies Offers 09–18.
	{ID: "19", Name: "Language Multiplier", Module: "media.dubbing", CommercialStatus: "operator-review-required", Tiers: []OfferTier{
		{ID: "starter", SetupMinor: 4000000, MonthlyMinor: 4500000, Currency: "NGN", Entitlements: map[string]int{"videos_per_month": 2, "languages": 1}},
		{ID: "growth", SetupMinor: 6000000, MonthlyMinor: 7500000, Currency: "NGN", Entitlements: map[string]int{"videos_per_month": 4, "languages": 2, "subtitles": 1}},
		{ID: "pro", SetupMinor: 10000000, MonthlyMinor: 12000000, Currency: "NGN", Entitlements: map[string]int{"videos_per_month": 8, "languages": 3, "series_handling": 1}},
	}},
	{ID: "20", Name: "Vibe-Code Audit", Module: "security.audit", CommercialStatus: "operator-review-required", Tiers: []OfferTier{
		{ID: "audit", PackMinor: 22000000, Currency: "NGN", Entitlements: map[string]int{"documented_findings": 1, "prioritized_fix_list": 1, "evidence_pack": 1}},
		{ID: "re-audit", MonthlyMinor: 8500000, Currency: "NGN", Entitlements: map[string]int{"release_re_audit": 1, "release_note_review": 1}},
	}},
	{ID: "21", Name: "Department Copilot", Module: "knowledge.copilot", CommercialStatus: "operator-review-required", Tiers: []OfferTier{
		{ID: "department", SetupMinor: 12000000, MonthlyMinor: 6500000, Currency: "NGN", Entitlements: map[string]int{"departments": 1, "call_preparation": 1, "same_day_follow_up": 1, "monday_pipeline_review": 1}},
	}},
	{ID: "22", Name: "Intent Radar", Module: "leads.intent", CommercialStatus: "operator-review-required", Tiers: []OfferTier{
		{ID: "daily-digest", SetupMinor: 3000000, MonthlyMinor: 5500000, Currency: "NGN", Entitlements: map[string]int{"opportunities_per_day": 5, "competitor_gripe_log": 1, "human_voice_review": 1}},
	}},
	{ID: "23", Name: "Monday Dashboard", Module: "analytics.dashboard", CommercialStatus: "operator-review-required", Tiers: []OfferTier{
		{ID: "monday", SetupMinor: 12000000, MonthlyMinor: 7000000, Currency: "NGN", Entitlements: map[string]int{"data_sources": 3, "kpi_metrics": 10, "monday_digest": 1}},
	}},
	{ID: "24", Name: "Paperless Office", Module: "documents.archive", CommercialStatus: "operator-review-required", Tiers: []OfferTier{
		{ID: "archive", SetupMinor: 10000000, MonthlyMinor: 5000000, Currency: "NGN", Entitlements: map[string]int{"searchable_archive": 1, "archive_qa": 1, "retention_policy": 1}},
	}},
	{ID: "25", Name: "Collections Engine", Module: "collections.receivables", CommercialStatus: "operator-review-required", Tiers: []OfferTier{
		{ID: "collections", SetupMinor: 5000000, MonthlyMinor: 5500000, Currency: "NGN", Entitlements: map[string]int{"reminder_steps": 4, "weekly_receivables_report": 1, "human_escalation": 1}},
	}},
	{ID: "26", Name: "Attribution Engine", Module: "marketing.attribution", CommercialStatus: "operator-review-required", Tiers: []OfferTier{
		{ID: "attribution", SetupMinor: 3000000, MonthlyMinor: 4000000, Currency: "NGN", Entitlements: map[string]int{"qr_artwork": 1, "monthly_attribution_report": 1, "whatsapp_clickthrough": 1, "keyword_fallback": 1}},
	}},
	// Volume III translates the eight video concepts into VolumeOS service
	// offers. External projects are inspirations, not deployed integrations;
	// delivery workflows and operator-approved commercial terms remain open.
	{ID: "27", Name: "Website Makeover Studio", Module: "web.makeover", CommercialStatus: "source-only-unpriced", Tiers: []OfferTier{}},
	{ID: "28", Name: "Video Ad Packs", Module: "media.ads", CommercialStatus: "source-only-unpriced", Tiers: []OfferTier{}},
	{ID: "29", Name: "Course Tutor", Module: "education.tutor", CommercialStatus: "source-only-unpriced", Tiers: []OfferTier{}},
	{ID: "30", Name: "AI Team Install", Module: "agents.teams", CommercialStatus: "source-only-unpriced", Tiers: []OfferTier{}},
	{ID: "31", Name: "Replacement Part Design", Module: "cad.parts", CommercialStatus: "source-only-unpriced", Tiers: []OfferTier{}},
	{ID: "32", Name: "Industry Audio Briefings", Module: "research.briefings", CommercialStatus: "source-only-unpriced", Tiers: []OfferTier{}},
	{ID: "33", Name: "Bid Radar", Module: "procurement.radar", CommercialStatus: "source-only-unpriced", Tiers: []OfferTier{}},
	{ID: "34", Name: "Website Guard", Module: "monitoring.website", CommercialStatus: "source-only-unpriced", Tiers: []OfferTier{}},
	// Creators Home: a YouTube production line (research, script, voice, scenes, thumbnail, SEO, assembly, upload)
	// on svc-creators. Unpriced until its tiers (videos per month, length) are set by the operator.
	{ID: "35", Name: "Creators Home", Module: "media.creators", CommercialStatus: "source-only-unpriced", Tiers: []OfferTier{}},
}

var q1Bundles = []Bundle{{
	ID: "growth-cadence-studio", SetupMinor: 5000000, MonthlyMinor: 9900000,
	Currency: "NGN", RequiredOffers: []string{"02:growth", "06:growth"}, StudioPacksMonth: 2,
}}

func OfferCatalog() ([]Offer, []Bundle) {
	offers := make([]Offer, len(q1Offers))
	copy(offers, q1Offers)
	for i := range offers {
		// Copies start from an empty slice: an unpriced offer has no tiers, and its list must encode as [] (not null).
		offers[i].Tiers = append(make([]OfferTier, 0, len(q1Offers[i].Tiers)), q1Offers[i].Tiers...)
		for j := range offers[i].Tiers {
			offers[i].Tiers[j].Entitlements = make(map[string]int, len(q1Offers[i].Tiers[j].Entitlements))
			for key, value := range q1Offers[i].Tiers[j].Entitlements {
				offers[i].Tiers[j].Entitlements[key] = value
			}
		}
	}
	bundles := append([]Bundle(nil), q1Bundles...)
	for i := range bundles {
		bundles[i].RequiredOffers = append(make([]string, 0, len(q1Bundles[i].RequiredOffers)), q1Bundles[i].RequiredOffers...)
	}
	return offers, bundles
}
