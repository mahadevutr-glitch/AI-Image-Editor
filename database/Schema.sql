-- ============================================================
-- VisionCraft AI - Complete Database Schema
-- Merged version with Cartoon Generation tables
-- ============================================================

-- Create database
CREATE DATABASE IF NOT EXISTS image_editor_db;
USE image_editor_db;

-- ============================================================
-- USERS TABLE
-- Stores user account information
-- ============================================================
CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(100) UNIQUE NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_login TIMESTAMP NULL,
    is_active BOOLEAN DEFAULT TRUE,
    INDEX idx_username (username),
    INDEX idx_email (email)
);

-- ============================================================
-- IMAGES TABLE
-- Stores uploaded images and their edit paths
-- ============================================================
CREATE TABLE IF NOT EXISTS images (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT,
    original_path VARCHAR(500) NOT NULL,
    edited_path VARCHAR(500),
    file_name VARCHAR(255),
    file_size INT,
    mime_type VARCHAR(100),
    width INT,
    height INT,
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL,
    INDEX idx_user_id (user_id),
    INDEX idx_uploaded_at (uploaded_at)
);

-- ============================================================
-- EDIT HISTORY TABLE
-- Tracks all edit operations performed by users
-- ============================================================
CREATE TABLE IF NOT EXISTS edit_history (
    id INT AUTO_INCREMENT PRIMARY KEY,
    image_id INT,
    user_id INT,
    operation_type VARCHAR(50) NOT NULL,
    operation_details TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (image_id) REFERENCES images(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL,
    INDEX idx_user_id (user_id),
    INDEX idx_image_id (image_id),
    INDEX idx_created_at (created_at),
    INDEX idx_operation_type (operation_type)
);

-- ============================================================
-- AI EFFECTS LOG TABLE
-- Logs all AI-powered effect usage
-- ============================================================
CREATE TABLE IF NOT EXISTS ai_effects_log (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT,
    effect_type VARCHAR(50) NOT NULL,
    image_id INT,
    processing_time_ms INT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL,
    FOREIGN KEY (image_id) REFERENCES images(id) ON DELETE SET NULL,
    INDEX idx_user_id (user_id),
    INDEX idx_effect_type (effect_type),
    INDEX idx_created_at (created_at)
);

-- ============================================================
-- CARTOON CONVERSIONS TABLE
-- Stores cartoon-style conversions (from code2 integration)
-- ============================================================
CREATE TABLE IF NOT EXISTS cartoon_conversions (
    id VARCHAR(36) PRIMARY KEY,
    user_id INT,
    original_filename VARCHAR(255),
    original_image LONGBLOB,
    cartoon_image LONGBLOB,
    style_used VARCHAR(50),
    method_used VARCHAR(100),
    created_at DATETIME,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL,
    INDEX idx_user_id (user_id),
    INDEX idx_style_used (style_used),
    INDEX idx_created_at (created_at)
);

-- ============================================================
-- SESSIONS TABLE (Optional - for production use)
-- Stores user session data
-- ============================================================
CREATE TABLE IF NOT EXISTS user_sessions (
    id VARCHAR(128) PRIMARY KEY,
    user_id INT,
    ip_address VARCHAR(45),
    user_agent TEXT,
    payload LONGTEXT,
    last_activity INT,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    INDEX idx_user_id (user_id),
    INDEX idx_last_activity (last_activity)
);

-- ============================================================
-- INSERT DEFAULT DEMO USER
-- Username: demo_user
-- Password: demo123 (hashed with SHA256)
-- ============================================================
INSERT INTO users (username, email, password_hash, created_at, is_active) 
VALUES (
    'demo_user', 
    'demo@visioncraft.com', 
    '007937d1cf8cb845ad3e1f5791c1074e64f7b60b64be656b85c13b288a91c78e',
    NOW(),
    TRUE
) ON DUPLICATE KEY UPDATE id=id;

-- ============================================================
-- OPTIONAL: INSERT SOME SAMPLE DATA FOR TESTING
-- Uncomment to add sample images and history
-- ============================================================

-- Sample image record (uncomment to use)
-- INSERT INTO images (user_id, original_path, edited_path, file_name, file_size, mime_type, uploaded_at)
-- VALUES (1, 'static/uploads/sample.jpg', 'static/uploads/sample.jpg', 'sample.jpg', 102400, 'image/jpeg', NOW());

-- Sample edit history (uncomment to use)
-- INSERT INTO edit_history (image_id, user_id, operation_type, operation_details)
-- VALUES (1, 1, 'filter', '{"type": "vibrant"}');

-- Sample AI effect log (uncomment to use)
-- INSERT INTO ai_effects_log (user_id, effect_type, image_id, processing_time_ms)
-- VALUES (1, 'background_remove', 1, 450);

-- ============================================================
-- VIEWS FOR REPORTS AND ANALYTICS
-- ============================================================

-- View: User activity summary
CREATE OR REPLACE VIEW user_activity_summary AS
SELECT 
    u.id AS user_id,
    u.username,
    u.email,
    u.created_at AS registered_at,
    COUNT(DISTINCT i.id) AS total_images,
    COUNT(DISTINCT eh.id) AS total_edits,
    COUNT(DISTINCT ae.id) AS total_ai_effects,
    COUNT(DISTINCT cc.id) AS total_cartoon_conversions,
    MAX(eh.created_at) AS last_edit_date
FROM users u
LEFT JOIN images i ON u.id = i.user_id
LEFT JOIN edit_history eh ON u.id = eh.user_id
LEFT JOIN ai_effects_log ae ON u.id = ae.user_id
LEFT JOIN cartoon_conversions cc ON u.id = cc.user_id
GROUP BY u.id, u.username, u.email, u.created_at;

-- View: Popular effects analytics
CREATE OR REPLACE VIEW popular_effects AS
SELECT 
    effect_type,
    COUNT(*) AS usage_count,
    AVG(processing_time_ms) AS avg_processing_time_ms,
    DATE(created_at) AS usage_date
FROM ai_effects_log
GROUP BY effect_type, DATE(created_at)
ORDER BY usage_count DESC;

-- View: Cartoon style popularity
CREATE OR REPLACE VIEW cartoon_style_popularity AS
SELECT 
    style_used,
    method_used,
    COUNT(*) AS conversion_count,
    DATE(created_at) AS conversion_date
FROM cartoon_conversions
GROUP BY style_used, method_used, DATE(created_at)
ORDER BY conversion_count DESC;

-- ============================================================
-- STORED PROCEDURES
-- ============================================================

DELIMITER //

-- Procedure: Clean up old sessions (older than 7 days)
CREATE PROCEDURE cleanup_old_sessions()
BEGIN
    DELETE FROM user_sessions 
    WHERE last_activity < UNIX_TIMESTAMP(DATE_SUB(NOW(), INTERVAL 7 DAY));
END //

-- Procedure: Get user statistics
CREATE PROCEDURE get_user_statistics(IN p_user_id INT)
BEGIN
    SELECT 
        (SELECT COUNT(*) FROM images WHERE user_id = p_user_id) AS total_images,
        (SELECT COUNT(*) FROM edit_history WHERE user_id = p_user_id) AS total_edits,
        (SELECT COUNT(*) FROM ai_effects_log WHERE user_id = p_user_id) AS total_ai_effects,
        (SELECT COUNT(*) FROM cartoon_conversions WHERE user_id = p_user_id) AS total_cartoon_conversions,
        (SELECT effect_type FROM ai_effects_log 
         WHERE user_id = p_user_id 
         GROUP BY effect_type 
         ORDER BY COUNT(*) DESC LIMIT 1) AS most_used_effect;
END //

-- Procedure: Get daily usage report
CREATE PROCEDURE get_daily_usage(IN p_date DATE)
BEGIN
    SELECT 
        'uploads' AS activity_type,
        COUNT(*) AS count
    FROM images
    WHERE DATE(uploaded_at) = p_date
    UNION ALL
    SELECT 
        'edits' AS activity_type,
        COUNT(*) AS count
    FROM edit_history
    WHERE DATE(created_at) = p_date
    UNION ALL
    SELECT 
        'ai_effects' AS activity_type,
        COUNT(*) AS count
    FROM ai_effects_log
    WHERE DATE(created_at) = p_date
    UNION ALL
    SELECT 
        'cartoon_conversions' AS activity_type,
        COUNT(*) AS count
    FROM cartoon_conversions
    WHERE DATE(created_at) = p_date;
END //

DELIMITER ;

-- ============================================================
-- TRIGGERS
-- ============================================================

-- Trigger: Update last_login timestamp
DELIMITER //
CREATE TRIGGER update_last_login 
BEFORE UPDATE ON users
FOR EACH ROW
BEGIN
    IF NEW.last_login IS NOT NULL AND OLD.last_login IS NULL THEN
        SET NEW.last_login = NOW();
    END IF;
END //
DELIMITER ;

-- ============================================================
-- INDEXES FOR PERFORMANCE OPTIMIZATION
-- ============================================================

-- Additional indexes for better query performance
CREATE INDEX idx_images_user_uploaded ON images(user_id, uploaded_at);
CREATE INDEX idx_history_user_created ON edit_history(user_id, created_at);
CREATE INDEX idx_ai_effects_user_created ON ai_effects_log(user_id, created_at);
CREATE INDEX idx_cartoon_user_created ON cartoon_conversions(user_id, created_at);

-- Full-text indexes for search functionality
ALTER TABLE users ADD FULLTEXT INDEX ft_username_email (username, email);
ALTER TABLE images ADD FULLTEXT INDEX ft_filename (file_name);

-- ============================================================
-- TABLE MAINTENANCE COMMENTS
-- ============================================================

-- To optimize tables periodically:
-- OPTIMIZE TABLE users, images, edit_history, ai_effects_log, cartoon_conversions;

-- To check table sizes:
-- SELECT 
--     table_name AS 'Table',
--     round(((data_length + index_length) / 1024 / 1024), 2) AS 'Size (MB)'
-- FROM information_schema.TABLES
-- WHERE table_schema = 'image_editor_db'
-- ORDER BY (data_length + index_length) DESC;

-- ============================================================
-- END OF SCHEMA
-- ============================================================