# arXiv project technical documentation

This is a deliberately compact documentation set: one overview, one architecture document, one shared data-contract document, one document per milestone, one integration document, one testing/performance document, and one development log.

All documents describe the checked-out `main` branch and distinguish implemented behavior from intended or unverified behavior.

## Navigation

- [00 Project overview](00_project_overview.md)
- [01 System architecture](01_system_architecture.md)
- [02 Data contracts](02_data_contracts.md)
- [03 M1 crawling](03_m1_crawling.md)
- [04 M2 parsing](04_m2_parsing.md)
- [05 M3 matching](05_m3_matching.md)
- [06 Integration](06_integration.md)
- [07 Testing and performance](07_testing_and_performance.md)
- [08 Development log](08_development_log.md)

## Project reference documents

- [Project overview PDF](Tổng-quan-Đồ-án-arXiv.pdf)
- [Work-allocation plan PDF](Ke_hoach_phan_chia_cong_viec_do_an_arXiv.pdf)

## Status vocabulary

- **Implemented:** executable behavior exists and is supported by tests or direct source evidence.
- **Partial:** some logic exists, but the complete stage is unfinished.
- **Skeleton:** the entry point raises `NotImplementedError`.
- **Unverified:** the repository cannot prove the claim.

## Official references

- [Project statement](https://hcmus-cs-ta.github.io/lab-book/intro-ds/statement/)
- [Milestone 1](https://hcmus-cs-ta.github.io/lab-book/intro-ds/statement/crawling/)
- [Milestone 2](https://hcmus-cs-ta.github.io/lab-book/intro-ds/statement/parsing/)
- [Milestone 3](https://hcmus-cs-ta.github.io/lab-book/intro-ds/statement/matching/)
