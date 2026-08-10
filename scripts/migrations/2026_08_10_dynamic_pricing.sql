-- migrate:up

CREATE TABLE IF NOT EXISTS pricing_versions (
    version_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    version_code VARCHAR(40) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'draft',
    note VARCHAR(255) NOT NULL DEFAULT '',
    created_by VARCHAR(64) NOT NULL,
    published_by VARCHAR(64) NULL,
    published_at DATETIME(3) NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (version_id),
    UNIQUE KEY uk_pricing_versions_code (version_code),
    KEY idx_pricing_versions_status_time (status, published_at),
    CONSTRAINT chk_pricing_versions_status CHECK (status IN ('draft', 'published', 'retired'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='版本化价格目录';

CREATE TABLE IF NOT EXISTS pricing_rules (
    rule_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    version_id BIGINT UNSIGNED NOT NULL,
    feature_code VARCHAR(64) NOT NULL,
    display_name VARCHAR(80) NOT NULL,
    billing_mode VARCHAR(24) NOT NULL,
    unit_name VARCHAR(32) NOT NULL,
    fixed_cost BIGINT UNSIGNED NOT NULL DEFAULT 0,
    base_cost BIGINT UNSIGNED NOT NULL DEFAULT 0,
    per_unit_cost BIGINT UNSIGNED NOT NULL DEFAULT 0,
    input_token_cost_per_1k BIGINT UNSIGNED NOT NULL DEFAULT 0,
    output_token_cost_per_1k BIGINT UNSIGNED NOT NULL DEFAULT 0,
    min_cost BIGINT UNSIGNED NOT NULL DEFAULT 0,
    max_cost BIGINT UNSIGNED NOT NULL DEFAULT 1000000,
    enabled TINYINT(1) NOT NULL DEFAULT 1,
    rule_meta JSON NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (rule_id),
    UNIQUE KEY uk_pricing_rules_version_feature (version_id, feature_code),
    KEY idx_pricing_rules_feature (feature_code, version_id),
    CONSTRAINT fk_pricing_rules_version FOREIGN KEY (version_id) REFERENCES pricing_versions(version_id) ON DELETE CASCADE,
    CONSTRAINT chk_pricing_rules_mode CHECK (billing_mode IN ('fixed', 'token', 'metered')),
    CONSTRAINT chk_pricing_rules_range CHECK (min_cost <= max_cost)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='价格规则';

ALTER TABLE bills ADD COLUMN pricing_version VARCHAR(40) NULL AFTER description;
ALTER TABLE bills ADD COLUMN pricing_snapshot JSON NULL AFTER pricing_version;
ALTER TABLE bills ADD COLUMN input_tokens BIGINT UNSIGNED NULL AFTER pricing_snapshot;
ALTER TABLE bills ADD COLUMN output_tokens BIGINT UNSIGNED NULL AFTER input_tokens;

INSERT INTO pricing_versions (version_code, status, note, created_by, published_by, published_at)
VALUES ('2026.08.10-initial', 'published', '动态定价初始目录，发布后可由后台调整', 'migration', 'migration', CURRENT_TIMESTAMP(3))
ON DUPLICATE KEY UPDATE version_code = VALUES(version_code);

INSERT INTO pricing_rules (
    version_id, feature_code, display_name, billing_mode, unit_name,
    fixed_cost, input_token_cost_per_1k, output_token_cost_per_1k, min_cost, max_cost, enabled
)
SELECT version_id, 'analysis_export', '语料分析导出', 'fixed', '次', 5, 0, 0, 5, 5, 1
FROM pricing_versions WHERE version_code = '2026.08.10-initial'
ON DUPLICATE KEY UPDATE feature_code = VALUES(feature_code);

INSERT INTO pricing_rules (
    version_id, feature_code, display_name, billing_mode, unit_name,
    fixed_cost, input_token_cost_per_1k, output_token_cost_per_1k, min_cost, max_cost, enabled
)
SELECT version_id, feature_code, display_name, 'token', '千 Token', 0, 1, 2, 1, 100000, 1
FROM pricing_versions
JOIN (
    SELECT 'ai_chat' AS feature_code, 'AI 聊天' AS display_name
    UNION ALL SELECT 'ai_insight', 'AI 解读'
    UNION ALL SELECT 'ai_report', 'AI 研究报告'
) defaults ON 1 = 1
WHERE version_code = '2026.08.10-initial'
ON DUPLICATE KEY UPDATE feature_code = VALUES(feature_code);

-- migrate:down

ALTER TABLE bills DROP COLUMN output_tokens;
ALTER TABLE bills DROP COLUMN input_tokens;
ALTER TABLE bills DROP COLUMN pricing_snapshot;
ALTER TABLE bills DROP COLUMN pricing_version;
DROP TABLE IF EXISTS pricing_rules;
DROP TABLE IF EXISTS pricing_versions;
