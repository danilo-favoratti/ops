---
id: R13
version: "1"
severidade: revisar
fundamento: "aquisição de gêneros alimentícios; locação de veículo automotor, prestada por pessoa jurídica especializada, que contemple o serviço de motorista;"
---

# R13 — Indício de fornecedor incompatível com a categoria

## Fundamento e alcance

A [fonte](../../cota-parlamentar.md) contém duas vedações distintas: aquisição
de gêneros alimentícios e locação de veículo automotor com serviço de motorista.
O fundamento acima reúne esses dois itens. Esta regra procura indícios no
cadastro do fornecedor; seu enquadramento comercial não comprova os produtos
ou serviços efetivamente cobrados na nota.

A categoria `13` representa fornecimento de alimentação. A categoria `120`
representa locação ou fretamento de veículos automotores. Esta versão limita
o controle a essas categorias e às listas explícitas abaixo, sem criar uma
classificação geral de compatibilidade entre qualquer CNAE e qualquer despesa.

## Fonte cadastral e correspondência

Usar `despesas_current` e os dados Receita importados por
[`fetch cnpj`](../../docs/runbooks/fetch.md). A razão social pertence à raiz de
oito dígitos da empresa. O CNAE principal pertence ao estabelecimento com o
CNPJ integral de quatorze dígitos do favorecido: não usar o CNAE de outra
filial apenas por compartilhar a raiz.

Comparar a razão social da Receita, normalizada pela função compartilhada.
O texto livre do fornecedor na despesa não substitui a razão social para esta
regra. O snapshot disponível não demonstra qual era o CNAE ou a razão social
na data da despesa; essa limitação deve acompanhar o indício.

## Lógica determinística

Na categoria `13`, produzir indício quando o CNAE principal for um destes:
`4711301`, `4711302` ou `4712100`; ou quando a razão social contiver um dos
tokens inteiros `SUPERMERCADO`, `SUPERMERCADOS`, `HIPERMERCADO`,
`HIPERMERCADOS`, `MINIMERCADO`, `MINIMERCADOS`, `MERCEARIA` ou `MERCEARIAS`.
Não procurar essas palavras como fragmentos dentro de outro token. O token
genérico `MERCADO` não é gatilho desta versão.

Na categoria `120`, produzir indício quando o CNAE principal for `4923002`;
ou quando a razão social contiver a expressão normalizada `COM MOTORISTA`,
`SERVICO DE MOTORISTA` ou `SERVICOS DE MOTORISTA`. A expressão
`SEM MOTORISTA`, por si só, não é um gatilho. A simples presença da palavra
`MOTORISTA` também não basta.

Uma correspondência gera um achado por documento com severidade `revisar`.
Outras categorias e fornecedores sem os indicadores explícitos não disparam
esta regra. Não tratar um CNAE, uma palavra na razão social ou a falta de
correspondência como prova definitiva do conteúdo da nota.

O valor do achado é o `valor_liquido` do documento, em centavos exatos. Ele
representa o gasto sinalizado, não uma conclusão de prejuízo ou de restituição.

## Cobertura da fonte e evidência

Verificar a cobertura da importação Receita corrente para as raízes dos
favorecidos com CNPJ numérico nas categorias `13` e `120`. A ausência de uma
relação exigida causa regra ignorada com motivo registrado. Se as relações
existirem, mas faltar importação bem-sucedida ou cobertura da raiz, a regra
falha sem publicar resultados parciais. Uma raiz solicitada e pesquisada nos
arquivos fornecidos pode não ter cadastro correspondente; essa ausência não é
prova de irregularidade e, sem outro indicador, não gera achado.

Registrar `cnpj`, `raiz_cnpj`, `numero_sub_cota`, `fornecedor_na_despesa`,
`razao_social`, `natureza_juridica`, `cnae_principal`, `motivos` e `expressoes`.
O fornecedor escrito na despesa é apenas contexto; a busca de expressões usa
a razão social da Receita. Os motivos possíveis são
`cnae_comercio_alimenticio`, `cnae_locacao_com_motorista` e
`expressao_na_razao_social`.

`origem_empresa` e `origem_estabelecimento` contêm `source_file`,
`source_sha256`, `source_member` e `source_record_number`, ou são nulos quando
o respectivo registro não existe. Uma expressão na razão social pode gerar
indício sem CNAE disponível; um CNAE do estabelecimento pode gerar indício
sem razão social disponível. Registrar essas ausências sem completá-las por
inferência.

Registrar também `snapshot_id`, `snapshot_sha256`,
`revisao_humana_obrigatoria = true`, `conteudo_da_nota_comprovado = false` e
`cadastro_na_data_despesa = false`. A cobertura da raiz se refere aos arquivos
fornecidos, sem certificar a completude de todas as partições nacionais.

## Exemplos sintéticos

Na categoria `13`, o estabelecimento com CNAE `4711301` gera indício, assim
como a razão social fictícia `SUPERMERCADO EXEMPLO LTDA`. A razão social
`MERCADO CULTURAL EXEMPLO LTDA`, sem CNAE listado, não dispara pelo token
`MERCADO`. Um CNAE listado em outra filial não satisfaz a correspondência do
estabelecimento favorecido.

Na categoria `120`, o CNAE `4923002` ou a razão social fictícia
`LOCACAO COM MOTORISTA EXEMPLO LTDA` gera indício. `LOCACAO SEM MOTORISTA
EXEMPLO LTDA`, sem CNAE listado, não dispara pela expressão negativa. Um
fornecedor com esses indicadores em outra categoria não dispara esta regra.

## Revisões de fonte e o que o agente deve verificar

Mudanças no snapshot ou no cadastro podem alterar a evidência de um achado
com a mesma chave de regra, versão e documento. Nesse caso, a checagem segue
o conflito de imutabilidade do
[ADR 0009](../../docs/adr/0009-immutable-finding-conflicts.md), preservando
achados e revisões anteriores.

O agente deve conferir a identificação integral do favorecido, o estabelecimento,
o CNAE principal, a razão social, o indicador registrado e os itens efetivos da
nota. Deve distinguir fornecimento de refeição de aquisição de gêneros e
verificar se a locação contempla motorista, usando os documentos disponíveis.
Registrar a ausência de evidência ou de histórico sem inventar itens, atividades
ou condições contratuais.

O agente anota o indício produzido pelo código; não decide seu disparo nem
converte o cadastro em prova de despesa vedada. A aprovação para o relatório
final cabe à revisão humana.
