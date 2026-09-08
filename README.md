# LeagueDeck

Self-hosted dashboard unifying fantasy football leagues across Sleeper,
ESPN, and Yahoo. See `docs/superpowers/specs/2026-09-07-leaguedeck-design.md`
for the full design.

This is the Phase 1a build: foundation + Sleeper only (read-only). ESPN,
Yahoo, and the injury-alerts/start-sit/waiver-wire features are follow-on
plans in `docs/superpowers/plans/`.

## Quick start

1. Copy `.env.example` to `.env`.
2. Generate the three required secrets:

   ```bash
   python3 -c "from passlib.context import CryptContext; print(CryptContext(schemes=['bcrypt']).hash(input('App password: ')))"
   python3 -c "import secrets; print(secrets.token_urlsafe(32))"
   python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

   Paste the three outputs into `.env` as `APP_PASSWORD_HASH`, `SECRET_KEY`,
   and `FERNET_KEY` respectively.

3. `docker-compose up --build`
4. Open `http://localhost:5173`, log in with the password you hashed above,
   go to Settings, enter your Sleeper username and league ID(s), then go to
   the dashboard.
