-- migrate:up

ALTER TABLE pricing_rules ADD COLUMN unit_size BIGINT UNSIGNED NOT NULL DEFAULT 1 AFTER unit_name;

SET @source_version_id := (
    SELECT version_id FROM pricing_versions
    WHERE status = 'published'
    ORDER BY published_at DESC, version_id DESC
    LIMIT 1
);

INSERT INTO pricing_versions (version_code, status, note, created_by)
VALUES (
    '2026.08.10-corpus-downloads',
    'draft',
    '加入 HSK、全球中介语下载与 HSK 作文导出按量计费',
    'migration'
)
ON DUPLICATE KEY UPDATE version_code = VALUES(version_code);

SET @target_version_id := (
    SELECT version_id FROM pricing_versions
    WHERE version_code = '2026.08.10-corpus-downloads'
    LIMIT 1
);

INSERT INTO pricing_rules (
    version_id, feature_code, display_name, billing_mode, unit_name, unit_size,
    fixed_cost, base_cost, per_unit_cost,
    input_token_cost_per_1k, output_token_cost_per_1k,
    min_cost, max_cost, enabled, rule_meta
)
SELECT
    @target_version_id, feature_code, display_name, billing_mode, unit_name, unit_size,
    fixed_cost, base_cost, per_unit_cost,
    input_token_cost_per_1k, output_token_cost_per_1k,
    min_cost, max_cost, enabled, rule_meta
FROM pricing_rules
WHERE version_id = @source_version_id
  AND feature_code NOT IN ('hsk_download', 'global_download', 'hsk_essay_export')
ON DUPLICATE KEY UPDATE
    display_name = VALUES(display_name),
    billing_mode = VALUES(billing_mode),
    unit_name = VALUES(unit_name),
    unit_size = VALUES(unit_size),
    fixed_cost = VALUES(fixed_cost),
    base_cost = VALUES(base_cost),
    per_unit_cost = VALUES(per_unit_cost),
    input_token_cost_per_1k = VALUES(input_token_cost_per_1k),
    output_token_cost_per_1k = VALUES(output_token_cost_per_1k),
    min_cost = VALUES(min_cost),
    max_cost = VALUES(max_cost),
    enabled = VALUES(enabled),
    rule_meta = VALUES(rule_meta);

INSERT INTO pricing_rules (
    version_id, feature_code, display_name, billing_mode, unit_name, unit_size,
    fixed_cost, base_cost, per_unit_cost,
    input_token_cost_per_1k, output_token_cost_per_1k,
    min_cost, max_cost, enabled
)
SELECT @target_version_id, feature_code, display_name, 'metered', unit_name, unit_size,
       0, 0, per_unit_cost, 0, 0, min_cost, 1000000, 1
FROM (
    SELECT 'hsk_download' AS feature_code, 'HSK 语料下载' AS display_name,
           '千条' AS unit_name, 1000 AS unit_size, 3 AS per_unit_cost, 3 AS min_cost
    UNION ALL
    SELECT 'global_download', '全球中介语语料下载', '千条', 1000, 3, 3
    UNION ALL
    SELECT 'hsk_essay_export', 'HSK 作文导出', '百篇', 100, 1, 1
) defaults
ON DUPLICATE KEY UPDATE
    display_name = VALUES(display_name),
    billing_mode = VALUES(billing_mode),
    unit_name = VALUES(unit_name),
    unit_size = VALUES(unit_size),
    per_unit_cost = VALUES(per_unit_cost),
    min_cost = VALUES(min_cost),
    max_cost = VALUES(max_cost),
    enabled = VALUES(enabled);

UPDATE pricing_versions
SET status = 'retired'
WHERE status = 'published' AND version_id <> @target_version_id;

UPDATE pricing_versions
SET status = 'published', published_by = 'migration', published_at = CURRENT_TIMESTAMP(3)
WHERE version_id = @target_version_id;

-- migrate:down

SET @rollback_version_id := (
    SELECT version_id FROM pricing_versions
    WHERE version_code = '2026.08.10-corpus-downloads'
    LIMIT 1
);

DELETE FROM pricing_versions WHERE version_id = @rollback_version_id;

UPDATE pricing_versions
SET status = 'published', published_by = 'migration-rollback', published_at = CURRENT_TIMESTAMP(3)
WHERE version_id = (
    SELECT version_id FROM (
        SELECT version_id FROM pricing_versions
        WHERE status = 'retired'
        ORDER BY published_at DESC, version_id DESC
        LIMIT 1
    ) previous_version
);

ALTER TABLE pricing_rules DROP COLUMN unit_size;
