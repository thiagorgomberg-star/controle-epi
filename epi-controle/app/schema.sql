-- Schema do sistema de Controle de EPI (PostgreSQL)

-- Guarda o conteúdo binário de fotos, assinaturas e logo diretamente no banco,
-- para que nada se perca quando o servidor reinicia (sem disco permanente).
CREATE TABLE IF NOT EXISTS arquivos (
    id SERIAL PRIMARY KEY,
    conteudo BYTEA NOT NULL,
    content_type TEXT NOT NULL,
    criado_em TEXT NOT NULL DEFAULT (to_char(now(), 'YYYY-MM-DD HH24:MI:SS'))
);

CREATE TABLE IF NOT EXISTS usuarios (
    id SERIAL PRIMARY KEY,
    nome TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    senha_hash TEXT NOT NULL,
    matricula TEXT UNIQUE,
    cpf TEXT,
    cargo TEXT,
    setor TEXT,
    telefone TEXT,
    papel TEXT NOT NULL DEFAULT 'colaborador' CHECK (papel IN ('colaborador', 'almoxarife', 'admin')),
    ativo INTEGER NOT NULL DEFAULT 1,
    criado_em TEXT NOT NULL DEFAULT (to_char(now(), 'YYYY-MM-DD HH24:MI:SS'))
);

-- Base oficial de CA (Certificado de Aprovação) sincronizada do Ministério do Trabalho
CREATE TABLE IF NOT EXISTS ca_cache (
    numero_ca TEXT PRIMARY KEY,
    situacao TEXT,
    validade TEXT,
    fabricante_cnpj TEXT,
    fabricante_nome TEXT,
    equipamento_nome TEXT,
    descricao TEXT,
    atualizado_em TEXT NOT NULL DEFAULT (to_char(now(), 'YYYY-MM-DD HH24:MI:SS'))
);

CREATE TABLE IF NOT EXISTS sync_log (
    id SERIAL PRIMARY KEY,
    executado_em TEXT NOT NULL DEFAULT (to_char(now(), 'YYYY-MM-DD HH24:MI:SS')),
    status TEXT NOT NULL,
    total_registros INTEGER,
    mensagem TEXT
);

CREATE TABLE IF NOT EXISTS epis (
    id SERIAL PRIMARY KEY,
    nome TEXT NOT NULL,
    descricao TEXT,
    fabricante TEXT,
    tamanho TEXT,
    ca_numero TEXT,
    ca_validade TEXT,
    vida_util_dias INTEGER NOT NULL DEFAULT 180,
    foto_arquivo_id INTEGER REFERENCES arquivos(id),
    estoque_atual INTEGER NOT NULL DEFAULT 0,
    estoque_minimo INTEGER NOT NULL DEFAULT 0,
    ativo INTEGER NOT NULL DEFAULT 1,
    criado_por INTEGER REFERENCES usuarios(id),
    criado_em TEXT NOT NULL DEFAULT (to_char(now(), 'YYYY-MM-DD HH24:MI:SS'))
);

CREATE TABLE IF NOT EXISTS estoque_movimentacoes (
    id SERIAL PRIMARY KEY,
    epi_id INTEGER NOT NULL REFERENCES epis(id),
    tipo TEXT NOT NULL CHECK (tipo IN ('entrada', 'saida', 'ajuste')),
    quantidade INTEGER NOT NULL,
    motivo TEXT,
    usuario_id INTEGER REFERENCES usuarios(id),
    criado_em TEXT NOT NULL DEFAULT (to_char(now(), 'YYYY-MM-DD HH24:MI:SS'))
);

CREATE TABLE IF NOT EXISTS entregas (
    id SERIAL PRIMARY KEY,
    epi_id INTEGER NOT NULL REFERENCES epis(id),
    colaborador_id INTEGER NOT NULL REFERENCES usuarios(id),
    direcionado_por INTEGER NOT NULL REFERENCES usuarios(id),
    quantidade INTEGER NOT NULL DEFAULT 1,
    tamanho TEXT,
    motivo TEXT NOT NULL DEFAULT 'Entrega inicial',
    data_entrega TEXT NOT NULL,
    data_troca_prevista TEXT,
    ca_numero TEXT,
    ca_validade TEXT,
    observacoes TEXT,
    status TEXT NOT NULL DEFAULT 'pendente' CHECK (status IN ('pendente', 'aceito', 'recusado')),
    criado_em TEXT NOT NULL DEFAULT (to_char(now(), 'YYYY-MM-DD HH24:MI:SS'))
);

CREATE TABLE IF NOT EXISTS aceites (
    id SERIAL PRIMARY KEY,
    entrega_id INTEGER NOT NULL UNIQUE REFERENCES entregas(id),
    assinatura_arquivo_id INTEGER NOT NULL REFERENCES arquivos(id),
    foto_arquivo_id INTEGER NOT NULL REFERENCES arquivos(id),
    aceito_em TEXT NOT NULL DEFAULT (to_char(now(), 'YYYY-MM-DD HH24:MI:SS')),
    ip_address TEXT,
    user_agent TEXT
);

CREATE TABLE IF NOT EXISTS configuracoes (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    empresa_nome TEXT,
    responsavel_sesmt TEXT,
    logo_arquivo_id INTEGER REFERENCES arquivos(id),
    atualizado_em TEXT NOT NULL DEFAULT (to_char(now(), 'YYYY-MM-DD HH24:MI:SS'))
);

CREATE INDEX IF NOT EXISTS idx_entregas_colaborador ON entregas(colaborador_id);
CREATE INDEX IF NOT EXISTS idx_entregas_status ON entregas(status);
CREATE INDEX IF NOT EXISTS idx_movimentacoes_epi ON estoque_movimentacoes(epi_id);
