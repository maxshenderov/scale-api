CREATE TABLE IF NOT EXISTS connections (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    url VARCHAR(255) NOT NULL,
    login VARCHAR(50) NOT NULL DEFAULT '',
    password VARCHAR(50) NOT NULL DEFAULT '',
    is_active BOOLEAN DEFAULT false
);

CREATE TABLE IF NOT EXISTS snapshots (
    id SERIAL PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    warehouse_name VARCHAR(100),
    data JSONB NOT NULL,
    is_active BOOLEAN DEFAULT false,
    created_at TIMESTAMP DEFAULT NOW()
);

INSERT INTO connections (name, url, login, password, is_active)
VALUES ('1C Test', 'http://it-mshenderov/1ctesterp5/hs/LikoRest/API', 'administrator', '224', true)
ON CONFLICT DO NOTHING;
