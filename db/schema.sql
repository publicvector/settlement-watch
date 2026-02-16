-- Settlement Watch Database Schema
-- Unified storage for all court and settlement data

-- Settlements from dorker
CREATE TABLE IF NOT EXISTS settlements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    amount REAL,
    amount_formatted TEXT,
    url TEXT,
    description TEXT,
    category TEXT,
    source TEXT,
    pub_date TEXT,
    guid TEXT UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- State court cases
CREATE TABLE IF NOT EXISTS state_cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    state TEXT NOT NULL,
    case_number TEXT,
    case_title TEXT,
    case_type TEXT,
    filing_date TEXT,
    court TEXT,
    county TEXT,
    parties TEXT,
    charges TEXT,
    status TEXT,
    url TEXT,
    raw_data TEXT,  -- JSON blob for extra fields
    guid TEXT UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Federal court cases (PACER)
CREATE TABLE IF NOT EXISTS federal_cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    court TEXT,
    case_number TEXT,
    case_title TEXT,
    case_type TEXT,
    filing_date TEXT,
    jurisdiction TEXT,
    nature_of_suit TEXT,
    parties TEXT,
    docket_entries TEXT,  -- JSON blob
    url TEXT,
    pacer_case_id TEXT,
    guid TEXT UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Docket entries (individual filings/events within cases)
CREATE TABLE IF NOT EXISTS docket_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER,  -- FK to state_cases or federal_cases
    case_source TEXT NOT NULL,  -- 'state' or 'federal'
    state TEXT,  -- For state cases
    case_number TEXT NOT NULL,
    entry_number INTEGER,  -- Docket entry number
    entry_date TEXT NOT NULL,
    entry_text TEXT,
    entry_type TEXT,  -- 'filing', 'order', 'opinion', 'hearing', 'minute', etc.
    is_opinion INTEGER DEFAULT 0,  -- Flag for judicial decisions
    is_order INTEGER DEFAULT 0,  -- Flag for court orders
    document_url TEXT,
    filed_by TEXT,  -- Party who filed (if applicable)
    judge TEXT,
    guid TEXT UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- DocumentCloud documents
CREATE TABLE IF NOT EXISTS documentcloud (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    source TEXT,
    organization TEXT,
    created_at TEXT,
    page_count INTEGER,
    document_url TEXT,
    pdf_url TEXT,
    category TEXT,
    is_court_doc INTEGER DEFAULT 0,
    is_settlement INTEGER DEFAULT 0,
    is_order INTEGER DEFAULT 0,
    imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Case outcomes: links complaints to final settlements
CREATE TABLE IF NOT EXISTS case_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    -- Case identification
    case_number TEXT,
    case_title TEXT,
    court TEXT,
    jurisdiction TEXT,  -- 'state' or 'federal'
    state TEXT,  -- if state court
    nature_of_suit TEXT,
    case_type TEXT,

    -- Complaint info
    complaint_date TEXT,
    complaint_url TEXT,
    complaint_pdf_url TEXT,
    initial_demand REAL,
    initial_demand_formatted TEXT,
    plaintiff TEXT,
    defendant TEXT,
    class_definition TEXT,  -- class description if class action
    estimated_class_size INTEGER,

    -- Settlement info
    settlement_date TEXT,
    settlement_amount REAL,
    settlement_amount_formatted TEXT,
    settlement_url TEXT,
    settlement_pdf_url TEXT,
    attorney_fees REAL,
    attorney_fees_formatted TEXT,
    actual_class_size INTEGER,  -- actual claimants
    per_claimant_amount REAL,
    claims_deadline TEXT,

    -- Computed/derived
    days_to_resolution INTEGER,  -- complaint_date to settlement_date
    outcome_ratio REAL,  -- settlement / initial_demand

    -- Linking to other tables
    settlement_id INTEGER,  -- FK to settlements table
    federal_case_id INTEGER,  -- FK to federal_cases
    state_case_id INTEGER,  -- FK to state_cases

    -- Metadata
    source TEXT,
    raw_data TEXT,  -- JSON blob for extra fields
    guid TEXT UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Feed metadata
CREATE TABLE IF NOT EXISTS feeds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    title TEXT,
    description TEXT,
    link TEXT,
    feed_url TEXT,
    last_build_date TIMESTAMP,
    item_count INTEGER DEFAULT 0
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_settlements_category ON settlements(category);
CREATE INDEX IF NOT EXISTS idx_settlements_pub_date ON settlements(pub_date);
CREATE INDEX IF NOT EXISTS idx_state_cases_state ON state_cases(state);
CREATE INDEX IF NOT EXISTS idx_state_cases_filing_date ON state_cases(filing_date);
CREATE INDEX IF NOT EXISTS idx_federal_cases_court ON federal_cases(court);
CREATE INDEX IF NOT EXISTS idx_federal_cases_filing_date ON federal_cases(filing_date);
CREATE INDEX IF NOT EXISTS idx_docket_entries_date ON docket_entries(entry_date);
CREATE INDEX IF NOT EXISTS idx_docket_entries_case ON docket_entries(case_number);
CREATE INDEX IF NOT EXISTS idx_docket_entries_type ON docket_entries(entry_type);
CREATE INDEX IF NOT EXISTS idx_docket_entries_opinion ON docket_entries(is_opinion);
CREATE INDEX IF NOT EXISTS idx_docket_entries_state ON docket_entries(state);
CREATE INDEX IF NOT EXISTS idx_case_outcomes_case ON case_outcomes(case_number);
CREATE INDEX IF NOT EXISTS idx_case_outcomes_court ON case_outcomes(court);
CREATE INDEX IF NOT EXISTS idx_case_outcomes_settlement_date ON case_outcomes(settlement_date);
CREATE INDEX IF NOT EXISTS idx_case_outcomes_amount ON case_outcomes(settlement_amount);
CREATE INDEX IF NOT EXISTS idx_case_outcomes_nature ON case_outcomes(nature_of_suit);
