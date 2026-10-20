---
id: R13
version: "3"
severidade: revisar
fundamento: "aquisição de gêneros alimentícios; locação de veículo automotor, prestada por pessoa jurídica especializada, que contemple o serviço de motorista;"
---

# R13 — Atividade do fornecedor exige conferência da nota

## Fundamento e alcance

A [fonte](../../cota-parlamentar.md) contém duas vedações distintas: aquisição
de gêneros alimentícios e locação de veículo automotor com serviço de motorista.
O fundamento acima reúne esses dois itens. Esta regra gera pistas cadastrais
para priorizar a conferência de notas e contratos. O enquadramento comercial
do fornecedor não comprova os produtos ou serviços efetivamente cobrados:
uma empresa pode exercer mais de uma atividade. O achado não afirma que a
despesa é vedada, nem permite concluir que houve prejuízo.

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

Antes de exigir cobertura da Receita, validar os dígitos verificadores do
CNPJ numérico e excluir identificadores de um único dígito repetido.
Um código de preenchimento, incompleto ou inválido não identifica um
estabelecimento, mesmo quando a raiz coincide com uma empresa real.
Esta versão não interpreta identificadores alfanuméricos.

A razão social da Receita e o texto livre do fornecedor na despesa são apenas
contexto. Nenhuma palavra ou expressão nesses nomes dispara a regra.
O snapshot disponível não demonstra qual era o CNAE ou a razão social
na data da despesa; essa limitação deve acompanhar o indício.

## Lógica determinística

Na categoria `13`, produzir indício quando o CNAE principal for um destes:
`4711301`, `4711302` ou `4712100`.

Na categoria `120`, produzir indício quando o CNAE principal for `4923002`.
Uma atividade cadastrada de locação com motorista não demonstra que a
locação analisada incluiu motorista. Confirmar essa condição na nota ou no
contrato antes de afirmar incompatibilidade.

Uma correspondência gera um achado por documento com severidade `revisar`.
Outras categorias e fornecedores sem esses CNAEs não disparam esta regra.
Não tratar um CNAE ou a falta de correspondência como prova do conteúdo da nota.
A ausência do CNAE principal não pode ser preenchida por inferência a partir
do nome da empresa. Esta versão não importa atividades secundárias.

O valor do achado é o `valor_liquido` do documento, em centavos exatos. Ele
representa o gasto sinalizado, não uma conclusão de prejuízo ou de restituição.

## Cobertura da fonte e evidência

Verificar a cobertura da importação Receita corrente para as raízes dos
favorecidos com CNPJ numérico válido nas categorias `13` e `120`. A ausência de uma
relação exigida causa regra ignorada com motivo registrado. Se as relações
existirem, mas faltar importação bem-sucedida ou cobertura da raiz, a regra
falha sem publicar resultados parciais. Uma raiz solicitada e pesquisada nos
arquivos fornecidos pode não ter cadastro correspondente; essa ausência não é
prova de irregularidade e, sem outro indicador, não gera achado.

Registrar `cnpj`, `raiz_cnpj`, `numero_sub_cota`, `fornecedor_na_despesa`,
`razao_social`, `natureza_juridica`, `cnae_principal` e `motivos`.
Os motivos possíveis são `cnae_comercio_alimenticio` e
`cnae_locacao_com_motorista`.

`origem_empresa` e `origem_estabelecimento` contêm `source_file`,
`source_sha256`, `source_member` e `source_record_number`, ou são nulos quando
o respectivo registro não existe. Um CNAE do estabelecimento pode gerar
indício sem razão social disponível. Registrar essas ausências sem
completá-las por inferência.

Registrar também `snapshot_id`, `snapshot_sha256`,
`revisao_humana_obrigatoria = true`, `indicio_apenas_cadastral = true`,
`conteudo_da_nota_comprovado = false`, `cadastro_na_data_despesa = false` e
`limitacao`, explicando a necessidade de conferir o documento da transação.
A cobertura da raiz se refere aos arquivos fornecidos, sem certificar a
completude de todas as partições nacionais.

## Exemplos sintéticos

Na categoria `13`, o estabelecimento com CNAE `4711301` gera indício.
Um restaurante com CNAE `5611201` e razão social fictícia
`SUPERMERCADO EXEMPLO LTDA` não dispara, assim como um fornecedor sem CNAE
disponível com esse nome. Um CNAE listado em outra filial não satisfaz a
correspondência do estabelecimento favorecido.

Na categoria `120`, o CNAE `4923002` gera pista para conferir a nota.
A razão social fictícia `LOCACAO COM MOTORISTA EXEMPLO LTDA`, sem esse CNAE,
não dispara. `LOCACAO SEM MOTORISTA EXEMPLO LTDA` tampouco é gatilho por nome;
se o estabelecimento tiver o CNAE `4923002`, o indício cadastral permanece,
com a limitação explícita de que o serviço contratado ainda não foi comprovado.
Um fornecedor com esses CNAEs em outra categoria não dispara esta regra.

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

## Ocorrências contábeis do mesmo documento (versão 2)

Preservar todas as linhas de origem. Quando um documento possui várias linhas,
reunir os indícios em um achado, com `ocorrencias` ordenadas e todos os hashes
em `registros_origem`. O valor é a soma exata dos valores líquidos das
ocorrências que dispararam a regra, incluindo ajustes negativos e valores zero.
Linhas sem indício não entram na soma, mas seus hashes permanecem no contexto
do documento. A origem principal é apenas uma referência, não todo o conteúdo
do achado. Não interpretar essa soma como prejuízo ou restituição devida.
