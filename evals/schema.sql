-- Seed schema for the pglens eval harness. Mirrors tests/conftest.py and adds
-- what the tasks need: a second table with an email column, a two-hop chain
-- (users -> orders -> payments), mixed-case status values, and comments.

CREATE TYPE order_status AS ENUM ('pending', 'confirmed', 'shipped', 'delivered');

CREATE TABLE users (
    id serial PRIMARY KEY,
    username varchar(100) NOT NULL UNIQUE,
    email text NOT NULL,
    bio text,
    created_at timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE users IS 'Application users';
COMMENT ON COLUMN users.id IS 'Auto-incrementing user ID';

CREATE TABLE customers (
    id serial PRIMARY KEY,
    company text NOT NULL,
    email text NOT NULL,
    country_code char(2) NOT NULL
);
COMMENT ON TABLE customers IS 'Billing contacts, one per company';

CREATE TABLE products (
    id serial PRIMARY KEY,
    name varchar(200) NOT NULL,
    price numeric(10,2) NOT NULL CHECK (price > 0),
    category varchar(50)
);

CREATE TABLE orders (
    id serial PRIMARY KEY,
    user_id int NOT NULL REFERENCES users(id),
    status order_status NOT NULL DEFAULT 'pending',
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE order_items (
    order_id int NOT NULL,
    product_id int NOT NULL,
    quantity int NOT NULL CHECK (quantity > 0),
    PRIMARY KEY (order_id, product_id),
    FOREIGN KEY (order_id) REFERENCES orders(id),
    FOREIGN KEY (product_id) REFERENCES products(id)
);

CREATE TABLE payments (
    id serial PRIMARY KEY,
    order_id int NOT NULL REFERENCES orders(id),
    amount numeric(10,2) NOT NULL,
    paid_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE shipping_regions (
    country_code char(2) NOT NULL,
    region_code varchar(10) NOT NULL,
    name text NOT NULL,
    PRIMARY KEY (country_code, region_code)
);

CREATE TABLE warehouses (
    id serial PRIMARY KEY,
    country_code char(2) NOT NULL,
    region_code varchar(10) NOT NULL,
    name text NOT NULL,
    FOREIGN KEY (country_code, region_code)
        REFERENCES shipping_regions(country_code, region_code)
);

-- Free-text status with inconsistent casing: the model must look before filtering.
CREATE TABLE tickets (
    id serial PRIMARY KEY,
    user_id int NOT NULL REFERENCES users(id),
    status text NOT NULL,
    subject text NOT NULL
);

CREATE TABLE api_keys (
    id serial PRIMARY KEY,
    token uuid NOT NULL,
    label text
);

CREATE VIEW active_orders AS
    SELECT o.id, o.user_id, u.username, o.status, o.created_at
    FROM orders o JOIN users u ON o.user_id = u.id
    WHERE o.status != 'delivered';

CREATE MATERIALIZED VIEW order_summary AS
    SELECT u.id AS user_id, u.username, count(o.id) AS order_count
    FROM users u LEFT JOIN orders o ON o.user_id = u.id
    GROUP BY u.id, u.username;
CREATE UNIQUE INDEX ON order_summary (user_id);

-- Never scanned: the unused-index task expects to find this one.
CREATE INDEX idx_users_bio ON users(bio);

CREATE SEQUENCE countdown INCREMENT -1 START 1000 MINVALUE 0 MAXVALUE 1000;
SELECT nextval('countdown');

CREATE FUNCTION update_modified_column() RETURNS trigger AS $$
BEGIN NEW.created_at = now(); RETURN NEW; END;
$$ LANGUAGE plpgsql VOLATILE;

CREATE TRIGGER trg_orders_modified BEFORE UPDATE ON orders
    FOR EACH ROW EXECUTE FUNCTION update_modified_column();

ALTER TABLE orders ENABLE ROW LEVEL SECURITY;
CREATE POLICY orders_owner_policy ON orders FOR SELECT
    USING (user_id = current_setting('app.current_user_id', true)::int);

INSERT INTO users (username, email, bio) VALUES
    ('alice', 'alice@example.com', 'Hello world'),
    ('bob', 'bob@example.com', NULL),
    ('charlie', 'charlie@example.com', 'Searching for 100% coverage');
INSERT INTO customers (company, email, country_code) VALUES
    ('Acme', 'billing@acme.example', 'US'),
    ('Globex', 'ap@globex.example', 'DE');
INSERT INTO products (name, price, category) VALUES
    ('Widget', 9.99, 'gadgets'), ('Gizmo', 19.99, 'gadgets'), ('Thingamajig', 4.50, 'misc');
INSERT INTO orders (user_id, status) VALUES (1, 'pending'), (1, 'shipped'), (2, 'confirmed');
INSERT INTO order_items (order_id, product_id, quantity) VALUES (1, 1, 2), (1, 2, 1), (2, 3, 5);
INSERT INTO payments (order_id, amount) VALUES (1, 29.97), (2, 22.50);
INSERT INTO shipping_regions (country_code, region_code, name) VALUES
    ('US', 'CA', 'California'), ('US', 'NY', 'New York');
INSERT INTO warehouses (country_code, region_code, name)
    VALUES ('US', 'CA', 'West Coast Warehouse');
INSERT INTO tickets (user_id, status, subject) VALUES
    (1, 'Open', 'Login fails'),
    (1, 'open', 'Slow dashboard'),
    (2, 'OPEN', 'Export broken'),
    (3, 'CLOSED', 'Typo on invoice'),
    (3, 'closed', 'Password reset');
INSERT INTO api_keys (token, label) VALUES
    ('11111111-2222-3333-4444-555555555555', 'ci token'),
    ('aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee', 'deploy token');

REFRESH MATERIALIZED VIEW order_summary;
ANALYZE;
