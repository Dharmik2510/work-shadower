-- Each person picks one of the built-in dot characters. Shown as their dot on the Mac and their
-- picture in the web app. Unknown values fall back to 'orb' in the clients.
ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar text NOT NULL DEFAULT 'orb'
    CHECK (avatar IN ('orb', 'sprout', 'ember', 'nimbus', 'pixel'));
