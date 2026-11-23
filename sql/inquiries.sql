-- C 端 AI 行程「找顾问」线索
CREATE TABLE IF NOT EXISTS quote_inquiries (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    user_id INT NOT NULL DEFAULT 1,
    customer_name VARCHAR(80) NOT NULL,
    customer_phone VARCHAR(40) NOT NULL DEFAULT '',
    city VARCHAR(80) NOT NULL,
    days INT NOT NULL,
    budget DECIMAL(12, 2) NOT NULL DEFAULT 0,
    preferences VARCHAR(500) NOT NULL DEFAULT '',
    plan_json LONGTEXT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'new',
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    PRIMARY KEY (id),
    KEY idx_inquiries_user (user_id),
    KEY idx_inquiries_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
