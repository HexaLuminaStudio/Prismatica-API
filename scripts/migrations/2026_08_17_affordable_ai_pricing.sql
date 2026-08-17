-- migrate:up

SET @source_version_id := (
    SELECT version_id FROM pricing_versions
    WHERE status = 'published'
    ORDER BY published_at DESC, version_id DESC
    LIMIT 1
);

INSERT INTO pricing_versions (version_code, status, note, created_by)
VALUES (
    '2026.08.17-affordable-ai',
    'draft',
    'AI 改为每百万 Token 加权合计计价，并采用低成本模型基准单价',
    'migration'
)
ON DUPLICATE KEY UPDATE version_code = VALUES(version_code);

SET @target_version_id := (
    SELECT version_id FROM pricing_versions
    WHERE version_code = '2026.08.17-affordable-ai'
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
  AND feature_code NOT IN ('ai_chat', 'ai_insight', 'ai_report')
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
    min_cost, max_cost, enabled, rule_meta
)
SELECT
    @target_version_id, feature_code, display_name, 'token', 'Token', 1000000,
    0, 0, 0, 1, 2, 1, 100000, enabled,
    JSON_OBJECT('tokenPricingVersion', 2)
FROM pricing_rules
WHERE version_id = @source_version_id
  AND feature_code IN ('ai_chat', 'ai_insight', 'ai_report')
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

UPDATE pricing_versions
SET status = 'retired'
WHERE status = 'published' AND version_id <> @target_version_id;

UPDATE pricing_versions
SET status = 'published', published_by = 'migration', published_at = CURRENT_TIMESTAMP(3)
WHERE version_id = @target_version_id;

-- migrate:down

SET @rollback_version_id := (
    SELECT version_id FROM pricing_versions
    WHERE version_code = '2026.08.17-affordable-ai'
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
