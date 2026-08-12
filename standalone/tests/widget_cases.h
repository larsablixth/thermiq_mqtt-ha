#ifndef THERMIQ_WIDGET_CASES_H
#define THERMIQ_WIDGET_CASES_H

struct case_state {
    const char *entity;
    const char *value;
};

struct widget_case {
    const char *name;
    const struct case_state *states;
    int count;
};

extern const struct widget_case WIDGET_CASES[];
extern const int WIDGET_CASE_COUNT;

#endif /* THERMIQ_WIDGET_CASES_H */
