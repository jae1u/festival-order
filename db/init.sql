CREATE DATABASE IF NOT EXISTS order_db;
USE order_db;

CREATE TABLE menus (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(50) UNIQUE,
    price INT,
    checks_required INT,
    image_url VARCHAR(255),
    is_soldout BOOLEAN DEFAULT FALSE
);

INSERT INTO menus (name, price, checks_required, image_url) VALUES
('냉동 삼겹살', 6000, 1, '/static/images/1.png'),
('우삼겹', 6000, 1, '/static/images/2.png'),
('미나리', 2000, 1, '/static/images/3.png'),
('짜파게티', 3000, 2, '/static/images/4.png'),
('즉석밥', 2000, 1, '/static/images/5.png'),
('펩시제로', 2000, 1, '/static/images/6.png'),
('칠성사이다', 2000, 1, '/static/images/7.png'),
('네모스넥S', 4000, 1, '/static/images/8.png'),
('네모스넥B', 8000, 1, '/static/images/9.png'),
('직원 호출', 0, 1, '/static/images/10.png');

CREATE TABLE table_sessions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    table_no INT NOT NULL,
    customer_name TEXT,
    customer_phone VARCHAR(50),
    organization VARCHAR(100),
    status VARCHAR(20) DEFAULT 'ACTIVE',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    paid_at DATETIME NULL
);

CREATE TABLE orders (
    id INT AUTO_INCREMENT PRIMARY KEY,
    session_id INT,
    order_time DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES table_sessions(id) ON DELETE CASCADE
);

CREATE TABLE order_items (
    id INT AUTO_INCREMENT PRIMARY KEY,
    order_id INT,
    menu_name VARCHAR(50),
    price INT,
    quantity INT,
    checks_required INT,
    checks_completed INT DEFAULT 0,
    FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE
);
