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

-- Campos tamanho/estoque_atual/estoque_minimo aqui embaixo são LEGADOS: o app
-- não lê nem grava mais neles (ver tabela epi_tamanhos, logo abaixo). Ficam na
-- tabela só para não apagar histórico de instalações antigas — nunca são
-- alterados por uma migração automática, então nenhum dado real é perdido.
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

-- Variantes de tamanho de um EPI, cada uma com seu próprio saldo de estoque
-- (ex.: Botina nº 40 / nº 42, cada tamanho com quantidade e mínimo próprios).
-- Um EPI sem variação real de tamanho ganha uma única variante "Único".
CREATE TABLE IF NOT EXISTS epi_tamanhos (
    id SERIAL PRIMARY KEY,
    epi_id INTEGER NOT NULL REFERENCES epis(id),
    tamanho TEXT NOT NULL,
    estoque_atual INTEGER NOT NULL DEFAULT 0,
    estoque_minimo INTEGER NOT NULL DEFAULT 0,
    ativo INTEGER NOT NULL DEFAULT 1,
    criado_em TEXT NOT NULL DEFAULT (to_char(now(), 'YYYY-MM-DD HH24:MI:SS'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_epi_tamanhos_unico ON epi_tamanhos(epi_id, tamanho);
CREATE INDEX IF NOT EXISTS idx_epi_tamanhos_epi ON epi_tamanhos(epi_id);

-- Migração idempotente: todo EPI cadastrado antes desta versão ganha uma
-- variante de tamanho única, herdando o tamanho/saldo que já tinha. Só insere
-- para EPIs que ainda não têm nenhuma variante, então roda sem duplicar nada
-- toda vez que o app sobe.
INSERT INTO epi_tamanhos (epi_id, tamanho, estoque_atual, estoque_minimo)
SELECT epis.id, COALESCE(NULLIF(TRIM(epis.tamanho), ''), 'Único'), epis.estoque_atual, epis.estoque_minimo
FROM epis
WHERE NOT EXISTS (SELECT 1 FROM epi_tamanhos et WHERE et.epi_id = epis.id);

CREATE TABLE IF NOT EXISTS estoque_movimentacoes (
    id SERIAL PRIMARY KEY,
    epi_id INTEGER NOT NULL REFERENCES epis(id),
    epi_tamanho_id INTEGER REFERENCES epi_tamanhos(id),
    tipo TEXT NOT NULL CHECK (tipo IN ('entrada', 'saida', 'ajuste')),
    quantidade INTEGER NOT NULL,
    motivo TEXT,
    usuario_id INTEGER REFERENCES usuarios(id),
    criado_em TEXT NOT NULL DEFAULT (to_char(now(), 'YYYY-MM-DD HH24:MI:SS'))
);
ALTER TABLE estoque_movimentacoes ADD COLUMN IF NOT EXISTS epi_tamanho_id INTEGER REFERENCES epi_tamanhos(id);

CREATE TABLE IF NOT EXISTS entregas (
    id SERIAL PRIMARY KEY,
    epi_id INTEGER NOT NULL REFERENCES epis(id),
    epi_tamanho_id INTEGER REFERENCES epi_tamanhos(id),
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
ALTER TABLE entregas ADD COLUMN IF NOT EXISTS epi_tamanho_id INTEGER REFERENCES epi_tamanhos(id);

-- Backfill idempotente: neste momento cada EPI antigo tem exatamente uma
-- variante (criada acima), então o vínculo é 1-para-1 e seguro. Uma vez
-- preenchido, uma linha nunca é tocada de novo (WHERE epi_tamanho_id IS NULL),
-- então adicionar um 2º tamanho no futuro não bagunça o histórico já ligado.
UPDATE estoque_movimentacoes m
SET epi_tamanho_id = et.id
FROM epi_tamanhos et
WHERE m.epi_tamanho_id IS NULL AND et.epi_id = m.epi_id;

UPDATE entregas en
SET epi_tamanho_id = et.id
FROM epi_tamanhos et
WHERE en.epi_tamanho_id IS NULL AND et.epi_id = en.epi_id;

CREATE INDEX IF NOT EXISTS idx_movimentacoes_epi_tamanho ON estoque_movimentacoes(epi_tamanho_id);
CREATE INDEX IF NOT EXISTS idx_entregas_epi_tamanho ON entregas(epi_tamanho_id);

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

-- Ajusta o fluxo de status da entrega para refletir separação/compra no
-- almoxarifado antes da retirada, em vez de pular direto para "aguardando
-- aceite". Dados antigos com status 'pendente' viram 'pronto_retirada'
-- automaticamente — antes, esse status já significava exatamente isso:
-- aguardando o colaborador retirar e assinar.
ALTER TABLE entregas DROP CONSTRAINT IF EXISTS entregas_status_check;
UPDATE entregas SET status = 'pronto_retirada' WHERE status = 'pendente';
ALTER TABLE entregas ADD CONSTRAINT entregas_status_check
  CHECK (status IN ('em_separacao', 'em_compra', 'pronto_retirada', 'aceito', 'recusado'));
ALTER TABLE entregas ALTER COLUMN status SET DEFAULT 'em_separacao';

-- Solicitações de troca/reposição feitas pelo colaborador. Depois de
-- aprovadas pelo gestor SESMT (admin), viram uma entrega normal (mesma
-- tabela entregas), que passa pelo mesmo fluxo de separação/compra/retirada.
CREATE TABLE IF NOT EXISTS solicitacoes (
    id SERIAL PRIMARY KEY,
    colaborador_id INTEGER NOT NULL REFERENCES usuarios(id),
    epi_tamanho_id INTEGER NOT NULL REFERENCES epi_tamanhos(id),
    tipo TEXT NOT NULL CHECK (tipo IN ('troca', 'reposicao')),
    quantidade INTEGER NOT NULL DEFAULT 1,
    motivo TEXT,
    status TEXT NOT NULL DEFAULT 'pendente_aprovacao' CHECK (status IN ('pendente_aprovacao', 'aprovada', 'recusada', 'cancelada')),
    aprovado_por INTEGER REFERENCES usuarios(id),
    aprovado_em TEXT,
    observacoes_aprovacao TEXT,
    entrega_id INTEGER REFERENCES entregas(id),
    criado_em TEXT NOT NULL DEFAULT (to_char(now(), 'YYYY-MM-DD HH24:MI:SS'))
);
CREATE INDEX IF NOT EXISTS idx_solicitacoes_colaborador ON solicitacoes(colaborador_id);
CREATE INDEX IF NOT EXISTS idx_solicitacoes_status ON solicitacoes(status);

-- Módulo de Ferramental: cadastro de ferramentas/equipamentos e liberação aos
-- colaboradores, nas duas modalidades pedidas — "emprestimo" (precisa
-- devolução, com previsão de data) e "fixo" (fica definitivamente com o
-- colaborador/equipe, sem devolução esperada, mas ainda pode ser devolvida
-- manualmente se a pessoa sair da empresa ou o item precisar voltar).
CREATE TABLE IF NOT EXISTS ferramentas (
    id SERIAL PRIMARY KEY,
    nome TEXT NOT NULL,
    descricao TEXT,
    fabricante TEXT,
    patrimonio TEXT,
    foto_arquivo_id INTEGER REFERENCES arquivos(id),
    estoque_atual INTEGER NOT NULL DEFAULT 0,
    estoque_minimo INTEGER NOT NULL DEFAULT 0,
    ativo INTEGER NOT NULL DEFAULT 1,
    criado_por INTEGER REFERENCES usuarios(id),
    criado_em TEXT NOT NULL DEFAULT (to_char(now(), 'YYYY-MM-DD HH24:MI:SS'))
);
CREATE INDEX IF NOT EXISTS idx_ferramentas_ativo ON ferramentas(ativo);

CREATE TABLE IF NOT EXISTS ferramenta_movimentacoes (
    id SERIAL PRIMARY KEY,
    ferramenta_id INTEGER NOT NULL REFERENCES ferramentas(id),
    tipo TEXT NOT NULL CHECK (tipo IN ('entrada', 'saida', 'ajuste')),
    quantidade INTEGER NOT NULL,
    motivo TEXT,
    usuario_id INTEGER REFERENCES usuarios(id),
    criado_em TEXT NOT NULL DEFAULT (to_char(now(), 'YYYY-MM-DD HH24:MI:SS'))
);
CREATE INDEX IF NOT EXISTS idx_ferramenta_mov_ferramenta ON ferramenta_movimentacoes(ferramenta_id);

CREATE TABLE IF NOT EXISTS liberacoes_ferramenta (
    id SERIAL PRIMARY KEY,
    ferramenta_id INTEGER NOT NULL REFERENCES ferramentas(id),
    colaborador_id INTEGER NOT NULL REFERENCES usuarios(id),
    direcionado_por INTEGER NOT NULL REFERENCES usuarios(id),
    quantidade INTEGER NOT NULL DEFAULT 1,
    tipo TEXT NOT NULL CHECK (tipo IN ('emprestimo', 'fixo')),
    motivo TEXT,
    data_liberacao TEXT NOT NULL,
    data_prevista_devolucao TEXT,
    observacoes TEXT,
    status TEXT NOT NULL DEFAULT 'em_separacao' CHECK (status IN ('em_separacao', 'em_compra', 'pronto_retirada', 'aceito', 'recusado', 'devolvido')),
    assinatura_arquivo_id INTEGER REFERENCES arquivos(id),
    foto_arquivo_id INTEGER REFERENCES arquivos(id),
    aceito_em TEXT,
    devolvido_em TEXT,
    devolvido_para INTEGER REFERENCES usuarios(id),
    observacoes_devolucao TEXT,
    criado_em TEXT NOT NULL DEFAULT (to_char(now(), 'YYYY-MM-DD HH24:MI:SS'))
);
CREATE INDEX IF NOT EXISTS idx_liberacoes_ferramenta_colaborador ON liberacoes_ferramenta(colaborador_id);
CREATE INDEX IF NOT EXISTS idx_liberacoes_ferramenta_status ON liberacoes_ferramenta(status);
CREATE INDEX IF NOT EXISTS idx_liberacoes_ferramenta_ferramenta ON liberacoes_ferramenta(ferramenta_id);
