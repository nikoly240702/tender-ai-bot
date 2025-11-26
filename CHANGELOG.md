# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Feature flags system via `config/features.yaml`
- Tender Sniper module scaffold (placeholder for Phase 2)
- Configuration loader for feature flags (`tender_sniper/config.py`)
- Comprehensive documentation for all planned modules
- Development roadmap in `tender_sniper/README.md`

### Changed
- Repository cleaned from cache and temporary files
- Reduced repository size by 2MB (removed logs, __pycache__, .DS_Store)

### Infrastructure
- Created backup branch before cleanup
- Modular architecture ready for feature toggling
- All existing functionality preserved and working

## [2024-11-26] - Repository Cleanup & Restructure

### Added
- `config/features.yaml` - Comprehensive feature flag configuration
- `tender_sniper/` - Module structure for Phase 2 development
  - `tender_sniper/bot/` - Enhanced Telegram bot (placeholder)
  - `tender_sniper/parser/` - Real-time parser (placeholder)
  - `tender_sniper/matching/` - Smart matching engine (placeholder)
  - `tender_sniper/notifications/` - Notification service (placeholder)
  - `tender_sniper/payments/` - Payment processing (placeholder)
  - `tender_sniper/database/` - Database models (placeholder)
  - `tender_sniper/admin/` - Admin dashboard (placeholder)
  - `tender_sniper/api/` - REST API (placeholder)
- `tender_sniper/config.py` - Feature configuration loader
- `tender_sniper/README.md` - Development roadmap and documentation
- `user_journey_test.py` - User journey testing utility

### Removed
- 18 `__pycache__/` directories (~500KB)
- All `*.pyc` compiled Python files
- 2 `*.log` files (including 1.4MB bot.log)
- 5 `.DS_Store` macOS metadata files
- Total: ~86 junk files removed, 2MB saved

### Changed
- Repository structure prepared for dual-product system
- Feature toggles enable selective component activation
- Non-destructive cleanup - all working code preserved

### Technical Details
- Backup branch created: `backup/pre-cleanup-20251126-143140`
- Git commits:
  - `b5bd4cc` - "chore: remove cached and temporary files"
  - `8b3015a` - "feat: add feature flags system and Tender Sniper scaffold"

## [2024-11-25] - Table Extraction Improvements

### Fixed
- Deep extraction from DOCX table cells with multiple paragraphs
- Nested table support (tables within cells)
- Extraction of numeric values and specifications from tables
- Universal goods filter (disabled server-side type filter for zakupki.gov.ru)

### Added
- Detailed logging for text extraction statistics
- Table preview in logs (first 1000 chars)
- Cell count tracking in DOCX extraction

## [2024-11-24] - V2.0 Beta Release

### Added
- Database caching for 70% token savings
- Batch processing (up to 20 tenders in parallel)
- Financial scoring v2.0 (margin, ROI, prepayment analysis)
- Smart document prioritization
- HTML batch reports with navigation

### Changed
- Multi-stage analysis architecture (6 stages)
- Premium model for critical stages
- Fast model for pre-filtering

## [2024-11-23] - Enhanced Search & Filtering

### Fixed
- Tender type filtering (purchaseObjectTypeCode)
- Medical goods search (disabled incorrect filters)
- Result multiplier increased (x3 → x5)

### Added
- Client-side filtering with smart indicators
- Detailed file type detection logging

## [2024-11-22] - PDF Extraction & File Type Detection

### Fixed
- PDF text extraction from corrupted files (pdftotext + OCR fallback)
- File type detection by magic bytes (not extension)
- DOCX files misidentified as PDF

### Added
- Automatic file renaming based on real type
- OCR support for scanned PDFs
- Docker dependencies (poppler, tesseract)

## Earlier Versions

See git history for details on versions prior to 2024-11-22.

---

## Legend

- **Added**: New features
- **Changed**: Changes in existing functionality
- **Deprecated**: Soon-to-be removed features
- **Removed**: Removed features
- **Fixed**: Bug fixes
- **Security**: Vulnerability fixes
- **Infrastructure**: DevOps, deployment, structure changes