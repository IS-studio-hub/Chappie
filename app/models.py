from pydantic import BaseModel, EmailStr, Field
from typing import Optional


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    name: str = Field(default="", max_length=120)
    pending_plan: Optional[str] = Field(
        default=None,
        description="Optional plan to open after email verification (small|mid|large)",
    )


class SigninRequest(BaseModel):
    email: EmailStr
    password: str


class CheckoutRequest(BaseModel):
    plan: str = Field(..., description="small | mid | large")


class SearchRequest(BaseModel):
    address: str = Field(..., description="Center address for the search")
    radius_km: float = Field(default=10.0, ge=0.1, le=50.0)
    max_results: int = Field(
        default=50,
        ge=1,
        le=200,
        description="Stop Places search once this many businesses without websites are found",
    )
    enrich_details: bool = Field(
        default=True,
        description="Scrape Google Maps for full details (products, reviews, etc.)",
    )
    find_emails: bool = Field(
        default=True,
        description="Find contact emails and generate outreach messages",
    )
    sender_business_info: str = Field(
        default="",
        description="Your business info used to personalize outreach emails",
    )
    target_categories: str = Field(
        default="",
        description="Comma-separated niches for category fit (e.g. dentist, salon, cafe)",
    )


class FigmaConnectRequest(BaseModel):
    token: str


class OpenAIConnectRequest(BaseModel):
    api_key: str


class Product(BaseModel):
    name: str
    price: Optional[str] = None
    category: Optional[str] = None


class Review(BaseModel):
    author: Optional[str] = None
    rating: Optional[int] = None
    text: Optional[str] = None


class BrandBook(BaseModel):
    """Visual/verbal brand system for website prototypes."""
    colors: list[str] = Field(default_factory=list, description="Hex brand colors")
    logo_description: Optional[str] = None
    logo_url: Optional[str] = None
    logo_svg: Optional[str] = Field(
        default=None,
        description="Inline SVG markup for the business logo (injected into Figma Make)",
    )
    tone: Optional[str] = None
    pricing_positioning: Optional[str] = None
    content_themes: list[str] = Field(default_factory=list)
    imagery_style: Optional[str] = None
    fonts_suggestion: Optional[str] = None
    source: Optional[str] = Field(default=None, description="heuristic | openai | cache")
    status: Optional[str] = Field(default=None, description="ready | pending | failed")
    notes: Optional[str] = None


class Business(BaseModel):
    place_id: Optional[str] = None
    name: str
    rating: Optional[float] = None
    review_count: Optional[int] = None
    category: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    hours: Optional[str] = None
    is_open: Optional[bool] = None
    areas_served: Optional[str] = None
    province: Optional[str] = None
    located_in: Optional[str] = None
    description: Optional[str] = None
    appointment_url: Optional[str] = None
    has_website: bool = False
    website_url: Optional[str] = None
    google_maps_url: Optional[str] = None
    photo_count: Optional[int] = None
    products: list[Product] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    reviews: list[Review] = Field(default_factory=list)
    social_profiles: dict[str, str] = Field(default_factory=dict)
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    contact_email: Optional[str] = None
    contact_emails: list[str] = Field(default_factory=list)
    email_source: Optional[str] = None
    outreach_subject: Optional[str] = None
    outreach_email: Optional[str] = None
    # Verified "no website" recheck (beyond Google Places websiteUri)
    no_website_score: Optional[int] = Field(
        default=None,
        description="0-100 confidence the business has no real website",
    )
    no_website_label: Optional[str] = None
    no_website_tier: Optional[str] = None
    no_website_status: Optional[str] = None
    no_website_signals: list[str] = Field(default_factory=list)
    suspected_website: Optional[str] = None
    # Composite lead quality (no-site, contact, category fit, reviews, freshness)
    lead_quality_score: Optional[int] = Field(
        default=None,
        description="0-100 outreach lead quality rank",
    )
    lead_quality_label: Optional[str] = None
    lead_quality_tier: Optional[str] = None
    lead_quality_signals: list[str] = Field(default_factory=list)
    learning_boost: Optional[int] = Field(
        default=None,
        description="Extra points from conversion learning loop (category/city)",
    )
    # Website / digital-service sales opportunity (0-100)
    website_opportunity_score: Optional[int] = Field(
        default=None,
        description="0-100 website & digital-service sales opportunity",
    )
    website_opportunity_label: Optional[str] = None
    website_opportunity_tier: Optional[str] = None
    website_opportunity_signals: list[str] = Field(default_factory=list)
    website_opportunity_breakdown: list[str] = Field(
        default_factory=list,
        description="Human-readable point breakdown, e.g. 'No website: +35'",
    )
    website_flags: dict[str, bool] = Field(
        default_factory=dict,
        description=(
            "Filterable digital gaps: no_website, outdated_website, not_mobile_friendly, "
            "slow_website, missing_https, missing_booking, missing_online_store, "
            "has_email, has_decision_maker"
        ),
    )
    brand_book: Optional[BrandBook] = Field(
        default=None,
        description="Brand colors, logo, tone, pricing, content, imagery for site drafts",
    )


class FigmaCreateSiteRequest(BaseModel):
    business: Business
    language: str = Field(
        default="English",
        max_length=80,
        description="Site UI/copy language for the Figma Make prompt",
    )


class GmailConnectRequest(BaseModel):
    email: str
    app_password: str


class SendEmailRequest(BaseModel):
    business: Business
    sender_business_name: str = ""
    sender_business_info: str = ""
    figma_prototype_link: str = ""
    subject: Optional[str] = None
    body: Optional[str] = None
    recipients: list[str] = Field(default_factory=list)
    template_id: str = Field(default="", max_length=40)
    logo_url: str = Field(
        default="",
        max_length=600_000,
        description="HTTPS logo URL or data:image/... base64 for the email header",
    )
    include_open_tracking: bool = Field(
        default=False,
        description="Embed open-tracking pixel (can hurt deliverability)",
    )
    include_unsubscribe_footer: bool = Field(
        default=True,
        description="Append unsubscribe/identity footer and List-Unsubscribe header",
    )


class PreviewEmailRequest(BaseModel):
    business: Business
    sender_business_name: str = ""
    sender_business_info: str = ""
    figma_prototype_link: str = ""
    language: str = Field(
        default="English",
        description="Target language for the email (e.g. French, Spanish, Arabic)",
        max_length=80,
    )
    template_id: str = Field(default="", max_length=40)
    logo_url: str = Field(default="", max_length=600_000)


class RenderEmailRequest(BaseModel):
    business_name: str = ""
    sender_business_name: str = ""
    sender_business_info: str = ""
    figma_prototype_link: str = ""
    body: str = ""
    template_id: str = Field(default="", max_length=40)
    logo_url: str = Field(default="", max_length=600_000)


class PipelineStatusRequest(BaseModel):
    status: str = Field(..., description="sent | opened | replied | booked | closed")
    notes: Optional[str] = None


class FavoriteBusinessRequest(BaseModel):
    business: Business


class SearchResponse(BaseModel):
    center_address: str
    center_lat: float
    center_lng: float
    radius_km: float
    total_found: int
    businesses: list[Business]


class SearchStatus(BaseModel):
    job_id: str
    status: str  # pending, running, completed, failed
    progress: int = 0
    total: int = 0
    message: str = ""
    result: Optional[SearchResponse] = None
    error: Optional[str] = None
