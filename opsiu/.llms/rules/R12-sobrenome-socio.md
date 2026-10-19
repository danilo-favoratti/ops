---
id: R12
version: "3"
severidade: revisar
fundamento: "bens ou serviços adquiridos de empresa ou entidade da qual o deputado ou parente até o terceiro grau ou servidor da Câmara seja proprietário ou detentor de qualquer participação;"
---

# R12 — Sobrenome compartilhado com sócio pessoa física

## Fundamento e alcance

A [fonte](../../cota-parlamentar.md) veda aquisições de empresa ou entidade
com participação do deputado, de parente até o terceiro grau ou de servidor da
Câmara. Esta regra usa uma coincidência de sobrenome como indício para revisão;
ela não comprova parentesco, grau de parentesco, participação do parlamentar
nem vínculo com servidor.

O quadro societário vem do conjunto Receita importado por
[`fetch cnpj`](../../docs/runbooks/fetch.md), filtrado pelas raízes de CNPJ dos
favorecidos das despesas correntes. O cadastro representa o snapshot disponível,
não um histórico de proprietários na data da despesa. A raiz de oito dígitos
identifica a empresa; o nome do sócio não deve ser confundido com a razão social.

## Campos e lógica determinística

Usar as despesas de `despesas_current` com CNPJ numérico de quatorze dígitos e
o quadro societário correspondente à raiz de oito dígitos. Considerar somente
sócios classificados pela Receita como pessoa física, identificador `2`.
Sócios pessoas jurídicas ou estrangeiros não entram nesta heurística.

Excluir contas institucionais de liderança identificadas por `cpf IS NULL` e
`sigla_uf = NA`, para não tratar seu nome como sobrenome de parlamentar.
Parlamentares com CPF ausente e UF válida continuam elegíveis.

Normalizar os nomes com a função compartilhada que remove acentos, uniformiza
maiúsculas e separadores. Excluir o primeiro token de cada nome. Comparar
apenas os tokens restantes com pelo menos quatro letras e fora da lista abaixo.
Uma coincidência elegível entre o parlamentar e um sócio pessoa física produz
um achado para revisão do documento. Não reconstruir CPF mascarado nem inferir
parentesco a partir da grafia coincidente.

O valor do achado é `valor_liquido` do documento, em centavos exatos. Ele
representa o gasto sinalizado para revisão, não uma perda comprovada ou um valor
automaticamente recuperável.

## Cobertura da fonte

A regra exige cobertura da importação Receita corrente para todas as raízes de
CNPJ numérico das despesas que examina. Se as relações exigidas não existirem,
o executor ignora a regra com motivo registrado. Se existirem, mas faltar uma
importação bem-sucedida ou a raiz não estiver no conjunto filtrado, a regra
falha sem publicar resultados parciais. Reimportar ou reproduzir o snapshot
após a entrada de novas raízes na base de despesas.

Uma raiz incluída na cobertura, mas sem sócio pessoa física encontrado nos
arquivos fornecidos, é um resultado sem correspondência. A cobertura comprova
que a raiz foi solicitada na filtragem daqueles arquivos; não certifica que
o diretório manual continha todas as partições da base nacional.

## Evidência

Registrar `cnpj`, `raiz_cnpj`, `nome_parlamentar_normalizado` e a lista ordenada
`tokens_compartilhados`. A lista `socios` contém somente as correspondências,
cada uma com `nome`, `qualificacao`, `source_file`, `source_sha256`,
`source_member`, `source_record_number` e seus `tokens_compartilhados`.

A origem externa fica em `snapshot_id` e `snapshot_sha256`. As limitações são
explícitas: `revisao_humana_obrigatoria = true`, `parentesco_comprovado = false`
e `cadastro_na_data_despesa = false`. O valor líquido fica no achado e no
registro de despesa associado; os arquivos e posições permitem reproduzir o
indício sem consultar uma versão futura da fonte.

## Lista de exclusão de sobrenomes

A lista inclui sobrenomes comuns e termos geracionais. Ela é uma política
explícita desta versão e deve corresponder exatamente à implementação.

<!-- surname-stoplist:start -->
```json
[
  "ALMEIDA",
  "ARAUJO",
  "BARBOSA",
  "CARVALHO",
  "COSTA",
  "DIAS",
  "FERREIRA",
  "FILHO",
  "GOMES",
  "JUNIOR",
  "LIMA",
  "MARTINS",
  "NASCIMENTO",
  "NETO",
  "OLIVEIRA",
  "PEREIRA",
  "RIBEIRO",
  "ROCHA",
  "RODRIGUES",
  "SANTOS",
  "SILVA",
  "SOBRINHO",
  "SOUSA",
  "SOUZA"
]
```
<!-- surname-stoplist:end -->

## Exclusão de prenomes (versão 3)

Além dos sobrenomes comuns, excluir os seguintes termos por política explícita
aprovada pelo revisor. Aplicar a exclusão aos nomes de parlamentares e de sócios,
após normalização de acentos e maiúsculas, independentemente da posição.
Esta lista não pretende classificar todos os prenomes da língua portuguesa;
termos não listados continuam sujeitos à revisão humana.

<!-- given-name-stoplist:start -->
```json
[
  "ANTONIO",
  "AUGUSTO",
  "CARLOS",
  "CESAR",
  "CRISTINA",
  "EDUARDO",
  "FABIANO",
  "HENRIQUE",
  "JOSE",
  "LUCAS",
  "LUIZ",
  "PAULA",
  "PAULO",
  "ROBERTO",
  "TADEU",
  "VICTOR"
]
```
<!-- given-name-stoplist:end -->

Uma coincidência apenas em `CARLOS` ou `JOSÉ`, por exemplo, não gera achado.
Se houver outro sobrenome elegível, preservar essa correspondência: `ANA
HENRIQUE VIEIRA` e `BEATRIZ HENRIQUE VIEIRA` continuam compartilhando `VIEIRA`,
mas a evidência não inclui `HENRIQUE`. Não ampliar a exclusão automaticamente
para nomes semelhantes ou termos não aprovados.

A versão 3 cria novas chaves de achado em uma nova execução de `check`. Achados,
pareceres e decisões da versão 2 são preservados; rejeições humanas anteriores
não são copiadas automaticamente para evidências da nova versão. Assim, um
achado anteriormente rejeitado em lote por `HENRIQUE` pode reaparecer para
revisão se também houver correspondência válida em `VIEIRA`.

## Exemplos de correspondência

Com nomes sintéticos, `ANA FIGUEIREDO` e a sócia pessoa física
`BEATRIZ FIGUEIREDO` compartilham o token elegível `FIGUEIREDO` e geram indício.
Os mesmos nomes com sócia classificada como pessoa jurídica não disparam.

`ANA SILVA` e `BEATRIZ SILVA` não disparam porque `SILVA` está na lista de
exclusão. Coincidência apenas no primeiro nome, token de três letras ou termo
geracional também não dispara. Ausência de coincidência não comprova ausência
de parentesco ou de participação vedada.

## Revisões de fonte e o que o agente deve verificar

Uma nova fonte pode alterar o sócio ou a evidência sob a mesma chave de regra,
versão e documento. A checagem pode então falhar por conflito de imutabilidade,
conforme o [ADR 0009](../../docs/adr/0009-immutable-finding-conflicts.md).
Preservar os achados e as revisões anteriores; não substituir silenciosamente
o snapshot que fundamentou o indício.

O agente deve conferir os nomes, os tokens efetivamente compartilhados, o tipo
do sócio, a raiz do fornecedor e a origem cadastral. Deve distinguir hipótese
de parentesco de vínculo documental comprovado, apontar a ausência de histórico
societário e indicar qual evidência adicional seria necessária para esclarecer
o vínculo na data da despesa. Não inventar relações familiares, CPFs ou datas.

A severidade `revisar` expressa a necessidade de verificação. O agente anota o
achado determinístico; a aprovação para o relatório final cabe à revisão humana.

## Identidade e ocorrências contábeis (versão 2)

Com identificador de documento positivo, reunir as ocorrências em um achado
por documento. Somar somente os valores líquidos das linhas que dispararam a
regra, inclusive ajustes negativos e zero, mantendo as evidências em
`ocorrencias` e os hashes de todas as linhas em `registros_origem`.

Quando `id_documento` é zero ou negativo, não reunir registros não relacionados.
A identidade inclui o ano do arquivo CEAP e o SHA-256 do registro original;
`identidade_registro` documenta ambos e a ausência de identificador. Registros
idênticos no mesmo arquivo anual compartilham identidade e preservam suas
ocorrências; registros distintos são achados separados, com origem própria.
O identificador original permanece inalterado. O ano da fonte, não sua posição,
caminho local ou identificador de importação, torna a identidade reproduzível.

Esses indícios continuam exigindo revisão humana; não comprovam parentesco,
participação societária vedada nem prejuízo recuperável.
